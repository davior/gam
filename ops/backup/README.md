# Backup setup: gam → Synology NAS

Sets up the `backup` service from `docker-compose.yml`: a sidecar that takes a
whole-database snapshot and pushes it, with the entire `data/media/` tree, to a
Synology NAS over SFTP on a schedule. See `docs/backup-strategy.md` for design
rationale. This file is the setup procedure.

All backup logic runs in the `backup` container on the gam host. Nothing
restic-specific runs on the NAS — it only needs its stock SFTP service.

gam runs on a separate remote host, so the NAS is reached over a router
port-forward. SSH is therefore genuinely exposed to the internet, and the steps
below lean on key-only auth, a non-default port, a locked-down account, a
source-IP firewall rule, and DSM's brute-force protection.

---

## Read this first: two undocumented DSM requirements

Two things are required that no Synology documentation mentions, and both fail
with an unhelpful `Permission denied (publickey,password)` and nothing useful in
the logs.

**1. `PubkeyAuthentication` is OFF in Synology's sshd build.** Stock OpenSSH
defaults it on. Synology's does not, and ships `sshd_config` with the line
commented out — which looks like "default applies" but isn't. Without an
explicit `PubkeyAuthentication yes`, sshd rejects every key at the offer stage
without ever reading `authorized_keys`.

**2. The backup account needs access to the `homes` share.** sshd reads
`~/.ssh/authorized_keys` as the target user. DSM puts an ACL on `/volume1/homes`,
and if the account is set to No Access on that share, sshd cannot traverse into
the account's own home. Symptom in a debug log:

```
debug1: Could not open authorized keys '.../authorized_keys': Permission denied
```

Setting `homes` to No Access looks like correct hardening. It silently breaks key
auth.

**Also worth knowing before you start:** DSM does not permit interactive SSH
shell logins for non-admin accounts. The backup account will never give you a
shell, by design. That is fine — restic uses the SFTP subsystem, not a shell.
Verify with `sftp`, never `ssh`.

Because of that, the way to run root commands on the NAS without enabling the SSH
terminal service is **Control Panel → Task Scheduler → Create → Scheduled Task →
User-defined script**, with User set to `root`. Untick "Enabled" so it never runs
on a schedule, then select it and click Run. Several steps below use this.

---

## 1. NAS: enable SFTP only

**Control Panel → File Services → FTP tab**

- Leave "Enable FTP service" and FTPS **unticked**. FTP and FTPS are different
  protocols from SFTP and you don't want them.
- Tick **Enable SFTP service**.
- Set a non-default port (e.g. `22022`). This is separate from the SSH port.
- Apply.

**Control Panel → Terminal & SNMP** — leave "Enable SSH service" **off**. SFTP
and SSH are independent services on DSM; SFTP works without the terminal service.

## 2. NAS: enable User Home service

**Control Panel → User & Group → Advanced tab → User Home** → tick **Enable user
home service** → Apply.

This is a global setting on the User & Group page, not a per-user one in the
account editor. It creates `/volume1/homes/<user>` for every account
automatically. No login required.

## 3. NAS: create the shared folder and account

**Control Panel → Shared Folder** → create a dedicated share, e.g. `GeckoNotes`.
Don't reuse a folder holding anything else.

**Control Panel → User & Group → User** → create a non-admin local user, e.g.
`gam-backup`.

**Permissions tab:**

| Share      | Access                                          |
| ---------- | ----------------------------------------------- |
| `GeckoNotes` | Read/Write                                    |
| `homes`    | Read Only (required — see "Read this first")     |
| everything else | No Access                                  |

**Applications tab:** Allow **SFTP** explicitly. Set **DSM** to Deny so the
account cannot reach the web UI. Deny everything else.

## 4. Generate the keypair (on the gam host)

Generate on the host that will run the backups, not on the NAS and not on your
laptop. Only the public half ever leaves this machine.

```bash
cd /opt/gam/ops/backup
mkdir -p secrets && chmod 700 secrets
ssh-keygen -t ed25519 -N "" -f secrets/id_backup
chmod 600 secrets/id_backup
cat secrets/id_backup.pub
```

No passphrase, because nothing can type one at 3am. `secrets/` is gitignored —
the private key must never be committed.

The comment at the end of the public key (`user@hostname`) is a free-text label.
sshd ignores it. It does not need to match the NAS account name.

## 5. NAS: install the public key

Task Scheduler script, run as root. Paste your public key into `PUBKEY` as a
**single unbroken line** — DSM's task editor is a plain textarea and a wrapped
key produces a file that looks fine and fails silently.

```bash
#!/bin/sh
USER_NAME=gam-backup
HOME_DIR=$(grep "^$USER_NAME:" /etc/passwd | cut -d: -f6)
PUBKEY="ssh-ed25519 AAAA...paste-yours-here... comment"

mkdir -p "$HOME_DIR/.ssh"
echo "$PUBKEY" > "$HOME_DIR/.ssh/authorized_keys"
chown -R "$USER_NAME:users" "$HOME_DIR" "$HOME_DIR/.ssh"
chmod 755 "$HOME_DIR"
chmod 700 "$HOME_DIR/.ssh"
chmod 600 "$HOME_DIR/.ssh/authorized_keys"

# verify
{
  ls -ld "$HOME_DIR" "$HOME_DIR/.ssh"
  ls -l "$HOME_DIR/.ssh/authorized_keys"
  wc -l "$HOME_DIR/.ssh/authorized_keys"
} > /volume1/GeckoNotes/keycheck.txt 2>&1
```

Read `keycheck.txt` via File Station. Home should be `755` owned by the user,
`.ssh` `700`, `authorized_keys` `600`, and the line count exactly **1**. A count
of 2+ means the key wrapped on paste.

The script derives `HOME_DIR` from `/etc/passwd` rather than hardcoding it. If
User Home service hasn't taken effect, a hardcoded `mkdir -p` will happily create
a directory tree sshd never consults.

Re-running this script overwrites `authorized_keys` rather than appending, so
it's safe to run again with a new key.

## 6. NAS: enable public key authentication

The step Synology doesn't document. Task Scheduler, root:

```bash
#!/bin/sh
CONF=/etc/ssh/sshd_config
cp "$CONF" "$CONF.bak"

sed -i '/^[[:space:]]*#*[[:space:]]*PubkeyAuthentication/d' "$CONF"
sed -i '/^[[:space:]]*#*[[:space:]]*AuthorizedKeysFile/d' "$CONF"
sed -i 's|^PasswordAuthentication yes|PasswordAuthentication yes\nPubkeyAuthentication yes\nAuthorizedKeysFile .ssh/authorized_keys|' "$CONF"

grep -v '^#' "$CONF" | grep -v '^$' > /volume1/GeckoNotes/sshdconf.txt 2>&1
```

Check `sshdconf.txt` shows both new lines **above** any `Match User` blocks.
Directives after a `Match` block apply only to those users.

Restart SFTP: Control Panel → File Services → FTP tab, untick Enable SFTP
service, Apply, re-tick, Apply. Then re-check `sshdconf.txt` — the toggle
sometimes rewrites `sshd_config`.

**This edit does not survive DSM updates.** See "Maintenance" below.

## 7. NAS: lock it down

- **Control Panel → Security → Protection** → enable **Auto Block** (e.g. 5
  attempts in 5 minutes, block permanently).
- **Control Panel → Security → Firewall** → allow the SFTP port only from the
  gam host's public IP; deny everything else on that port.
- **Router** → forward an external non-default port to the NAS's SFTP port.
  Restrict the forward to the source IP if your provider allows it.

Get the gam host's real outbound address from the host itself, not by
resolving its domain — a proxy or CDN in front of the app will give you the wrong
answer:

```bash
curl -4 ifconfig.me
```

If that host's IP is dynamic, a source-IP rule will silently break your backups
when it rotates.

## 8. gam host: pin the NAS host key

```bash
cd /opt/gam
ssh-keyscan -p <port> <ddns-or-public-ip> > ops/backup/secrets/known_hosts
ssh-keygen -lf ops/backup/secrets/known_hosts
```

`ssh-keyscan` verifies nothing — it accepts whatever answers. Compare the
fingerprint against the NAS's real key. **DSM does not display a host key
fingerprint anywhere in the UI**, so get it with a Task Scheduler script:

```bash
#!/bin/sh
for f in /etc/ssh/ssh_host_*_key; do ssh-keygen -lf "$f"; done \
  > /volume1/GeckoNotes/hostkeys.txt
```

Match the ED25519 line. That comparison is what establishes trust; skipping it
defeats the point of `StrictHostKeyChecking`. Delete `hostkeys.txt` afterwards.

**`known_hosts` must exist as a real file before any `docker compose` command.**
`docker-compose.yml` bind-mounts it, and Docker creates a **directory** at any
bind-mount source that doesn't exist. Nothing errors; you just get an empty
directory where a file should be, and confusing failures later. Same applies to
`id_backup`. If you find directories there:

```bash
rmdir ops/backup/secrets/id_backup ops/backup/secrets/known_hosts
```

## 9. Test SFTP with the key

The gate. Nothing downstream works until this does.

```bash
sftp -i ops/backup/secrets/id_backup \
     -o IdentitiesOnly=yes \
     -o PreferredAuthentications=publickey \
     -o UserKnownHostsFile=ops/backup/secrets/known_hosts \
     -P <port> <user>@<host>
```

`PreferredAuthentications=publickey` matters. Without it a broken key silently
falls back to the password and you won't discover the problem until the container
fails unattended.

Once connected:

```
sftp> pwd
sftp> ls
sftp> cd GeckoNotes
sftp> put /etc/hostname writetest.txt
sftp> rm writetest.txt
```

Synology chroots the user, so `pwd` at login returns `/` — that is the chroot
root, not the filesystem root. The share appears inside it as `/GeckoNotes`. Use
that, never `/volume1/GeckoNotes`.

## 10. Fill `.env`

```bash
openssl rand -base64 32
```

Save that string in a password manager **before** using it. restic encrypts
client-side; losing it makes every existing snapshot permanently unrecoverable.

```
RESTIC_PASSWORD=<generated above>
BACKUP_SFTP_HOST=<ddns hostname or public IP>
BACKUP_SFTP_PORT=<forwarded port>
BACKUP_SFTP_USER=gam-backup
BACKUP_SFTP_REMOTE_PATH=/GeckoNotes/restic
```

The `/restic` subdirectory keeps the repo's `data/`, `snapshots/` and `keys/`
from scattering across the share root. `restic init` creates it.

Edit `.env`, not `.env.example` — that stays a template.

## 11. Init and first run

```bash
docker compose build backup
docker compose run --rm backup restic init
docker compose run --rm backup /usr/local/bin/backup.sh
docker compose up -d backup
```

`restic init` is one-time and creates the repository on the NAS. The third line
forces an immediate backup instead of waiting for the first cron tick.

`docker compose run --rm` creates a throwaway container each time, so it always
picks up current files. `docker compose restart` does **not** — bind mounts and
environment variables are resolved at container *creation*. After changing `.env`
or anything in `secrets/`:

```bash
docker compose up -d --force-recreate backup
```

## 12. Verify

```bash
docker compose run --rm backup restic snapshots
docker logs backup
docker inspect <container> --format '{{.HostConfig.RestartPolicy.Name}}'
docker compose exec backup crontab -l
```

One snapshot listed means the chain works. Confirm the restart policy is
`unless-stopped` or `always`, or the container won't return after a host reboot.
Check `docker logs backup` again after the next scheduled tick — an unattended
run is the only real proof.

---

## Maintenance

**The `sshd_config` edit from step 6 does not survive DSM updates**, and may not
survive toggling the SFTP service. When it's reverted, backups fail silently.

Add a **Triggered Task → Boot-up**, User `root`, with the step 6 script body.
Re-run it manually after any DSM update, and re-check `sshdconf.txt`.

Consider putting Tailscale on both machines and dropping the port-forward
entirely. Synology has a package; the host takes one command. The backup then
runs over a private tailnet address, nothing is exposed to the internet, and the
firewall rule, non-default port and Auto Block stop being load-bearing.

---

## Troubleshooting

The client-side error is always the same unhelpful `Permission denied
(publickey,password)`. Get the server's actual reason instead.

### Read sshd's reasoning directly

Task Scheduler, root. Runs a debug sshd on a spare port that logs why it refused:

```bash
#!/bin/sh
timeout 180 /usr/bin/sshd -ddd -p 2299 -f /etc/ssh/sshd_config \
  > /volume1/GeckoNotes/sshd_debug.txt 2>&1
```

Connect to port 2299 within three minutes from a machine on the LAN (that port
isn't forwarded). It accepts one connection and exits.

Note: Synology's sshd only enables the sftp subsystem on the *configured* SFTP
port, so a debug daemon on another port will authenticate you and then report
`subsystem request failed`. That's expected. Authentication is what you're
testing.

What to look for:

| Log line | Cause |
| --- | --- |
| `Could not open authorized keys: Permission denied` | `homes` share access — step 3 |
| No `trying public key file` line at all | `PubkeyAuthentication` off — step 6 |
| `matching key found` then `Accepted publickey` | Auth is working |
| `Authentication refused: bad ownership or modes` | Permissions on home/`.ssh`/`authorized_keys` — step 5 |

### Check the client is offering the key

```bash
sftp -vvv -i <key> -o IdentitiesOnly=yes -P <port> <user>@<host>
```

`Offering public key:` followed by no `Server accepts key:` means the server
rejected it before requesting a signature — server-side, not a client problem.
Compare the offered fingerprint against what's stored on the NAS:

```bash
ssh-keygen -lf <path>/id_backup.pub          # on the host
ssh-keygen -lf <home>/.ssh/authorized_keys   # on the NAS, via Task Scheduler
```

### Things that look like the problem but aren't

Each of these was tried during the original setup and none of them mattered:

- **Login shell `/sbin/nologin`.** DSM assigns it to non-admin accounts and
  resets it on reboot. Irrelevant here — `internal-sftp` needs no shell.
- **Membership of `administrators`.** Required for interactive SSH shells, not
  for SFTP key auth. Don't grant it; it puts a privileged account behind an
  internet-facing port.
- **`chmod 711` on `.ssh`.** Frequently recommended online. `700` is correct and
  works.
- **`StrictModes` on parent directories.** Worth checking, but DSM's defaults
  (`/volume1/homes` at `711` root-owned) are already correct.

### Other notes

- **`synoservicectl` doesn't exist on DSM 7.** It's `synosystemctl`.
- **File Station won't show `.ssh`** (hidden directory) and can't reliably set
  Unix modes on ACL-managed shares. Use Task Scheduler scripts.
- **`/var/services/homes` is a symlink to `/volume1/homes`.** Either path works.

---

## Later: second destination

`docs/backup-strategy.md` describes fanning the same backup out to an off-site
S3-compatible bucket for geographic redundancy. Deliberately deferred. Adding it
means a second `restic backup ... -r <bucket-repo>` and its own `restic forget`
in `backup.sh`, with separate credentials. No changes to the NAS setup above.
