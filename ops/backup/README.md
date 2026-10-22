# Backup setup: gam → Synology NAS

Sets up the `backup` service from `docker-compose.yml`, a sidecar on the gam host that,
on a schedule:

1. takes a consistent snapshot of the SQLite database;
2. pushes it, with the whole `data/media/` tree, into an encrypted **restic**
   repository on the NAS — the history, kept 48 hours / 14 days / 8 weeks;
3. optionally keeps a **plain-file mirror** beside it — the same `db/` and
   `media/<owner>/<uuid>.<ext>` layout as `data/`, as ordinary files you can open, and
   that a read-only copy of the app can run from ([`ops/archive/`](../archive/README.md)).

Why both. restic is what saves you from a mistake: a deleted or overwritten file is
still in last week's snapshot. But its repository is encrypted chunks — nothing on the
NAS can open it without restic and the password. The mirror is the opposite: browsable,
runnable, and it copies deletions, so it is a working copy and not a history. Each
covers the other's gap.

**The mirror doubles the space media takes on the NAS**, and the first mirror run sends
the whole library over again. Size the share for twice `du -sh data/media`.

All backup logic runs in the `backup` container on the gam host. The NAS only needs its
stock SFTP service: restic and rclone both speak SFTP, and both use the one account and
key set up below.

gam runs on a separate remote host, so the NAS is reached over a router port-forward.
SSH is therefore genuinely exposed to the internet, and the steps below lean on key-only
auth, a non-default port, a locked-down account, a source-IP firewall rule, and DSM's
brute-force protection. (Or skip the exposure entirely with Tailscale — see
"Maintenance".)

Paths below assume the repo lives at `/opt/gam` on the gam host, and a share named `GAM`
on volume 1 of the NAS. Adjust both to taste, consistently.

**Steps 1–12 are the restic backup, 13–14 the mirror. Do them in order** — the mirror
reuses everything the earlier steps set up.

---

## Already backing up Gecko Notes from this server?

Then most of this is done, and GAM can reuse Notes' connection — the same account, key
and pinned host key. Notes' backup proves the hard parts work: SFTP enabled (step 1),
User Home service (2), the `PubkeyAuthentication` fix (6), Auto Block, firewall and
port-forward (7). Skip those.

Sharing the key costs nothing here. Both apps' secrets sit on the same server, so anyone
who can read one key can read the other; a second key would protect against nothing.
Keep the **repositories** separate, though — each app gets its own directory and its own
restic password.

What is left:

**a. Pick GAM's directory — inside Notes' share.** Notes' backup account already has
Read/Write on `GeckoNotes` (`/volume1/GeckoNotes`), and its restic repository sits at
`/GeckoNotes/restic`. GAM goes beside it in a directory of its own, so nothing on the
NAS changes at all — no new share, no permission edits on an account Notes depends on:

```
/volume1/GeckoNotes/
├── restic/          Notes' repository — untouched
└── gam/
    ├── restic/      GAM's repository (step 11 creates it)
    └── mirror/      GAM's plain-file mirror (step 13 creates it)
```

Check the prefix against Notes' own `.env` first: GAM's paths start with whatever
Notes' `BACKUP_SFTP_REMOTE_PATH` starts with. `/GeckoNotes/restic` there means
`/GeckoNotes/gam/…` here. (If it is plain `/restic`, the account's chroot *is* the share
and GAM's paths are `/gam/…`.)

If the `GeckoNotes` share has its **Recycle Bin** on, turn it off or the mirror's
deliberate deletions pile up in `#recycle` (step 3 explains). Notes' restic prune
deletes files too, so this was already worth doing.

**b. Copy Notes' key and host key** from its checkout at `/opt/gecko-notes` (adjust if
yours lives elsewhere):

```bash
mkdir -p /opt/gam/ops/backup/secrets && chmod 700 /opt/gam/ops/backup/secrets
cp /opt/gecko-notes/ops/backup/secrets/id_backup   /opt/gam/ops/backup/secrets/
cp /opt/gecko-notes/ops/backup/secrets/known_hosts /opt/gam/ops/backup/secrets/
chmod 600 /opt/gam/ops/backup/secrets/id_backup
```

Copies rather than a bind mount of Notes' files, so a change to one checkout — a
`git clean`, a key rotation half done — cannot take the other app's backup down with it.

**c. Test it** — step 9, with the copied key and Notes' host, port and user, then
`cd GeckoNotes` and the `put`/`rm` write test. It should pass first time; Notes' backup
already does exactly this every hour.

**d. Fill `.env`** as step 10, with two rules:

- `BACKUP_SFTP_HOST`, `BACKUP_SFTP_PORT` and `BACKUP_SFTP_USER` exactly as in Notes'
  `.env`. `known_hosts` pins the key under the host *as written*: Notes' entry for
  `nas.example.ddns.net` does not cover the same NAS reached as `203.0.113.7`, and
  the run fails with `Host key verification failed`.
- A **new** `RESTIC_PASSWORD`, generated as step 10 says and saved in the password
  manager under GAM. It encrypts GAM's repository, not the connection, and has nothing
  to do with Notes'.

```
BACKUP_SFTP_REMOTE_PATH=/GeckoNotes/gam/restic
BACKUP_MIRROR_PATH=
```

Never Notes' `/GeckoNotes/restic`. restic would happily put two apps' snapshots in one
repository, and `--host gam` would keep their retention apart — but the restic password would then be
shared, and a repository corrupted by either app would take both histories with it.

**e. Carry on from step 11** — including "A large first upload" — then 13–14 for the mirror, with
`BACKUP_MIRROR_PATH=/GeckoNotes/gam/mirror` (and that path wherever step 14 says
`/GAM/mirror`). On the NAS the mirror is then `/volume1/GeckoNotes/gam/mirror` — the
`GAM_MIRROR_DIR` the archive needs ([`ops/archive/README.md`](../archive/README.md),
step 4).

Two schedules now write to one NAS over one link. GAM's defaults to the top of the hour,
and so does Notes' (`0 * * * *`), so check `BACKUP_CRON_SCHEDULE` in Notes' `.env` and
offset GAM's from it — say `30 * * * *` — so the two uploads do not compete.

**Later, if the key is rotated** for Notes, copy the new one here again (b), or GAM's
backup starts failing the hour the old key is removed from the NAS.

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

**Control Panel → Shared Folder** → create a dedicated share, e.g. `GAM`.
Don't reuse a folder holding anything else. Leave "Encrypt this shared folder" off unless
you know you want it: an encrypted share is unmounted after every reboot until someone
types its key, and backups (and the archive) fail until they do. The restic repository is
encrypted regardless; the mirror is not — it is plain files, readable by any NAS account
granted the share.

Untick **Enable Recycle Bin** for this share: the
mirror deletes files on purpose, and a recycle bin quietly keeps every one of them,
filling the volume.

**Control Panel → User & Group → User** → create a non-admin local user, e.g.
`gam-backup`.

**Permissions tab:**

| Share      | Access                                          |
| ---------- | ----------------------------------------------- |
| `GAM` | Read/Write                                    |
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
} > /volume1/GAM/keycheck.txt 2>&1
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

grep -v '^#' "$CONF" | grep -v '^$' > /volume1/GAM/sshdconf.txt 2>&1
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
  > /volume1/GAM/hostkeys.txt
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
sftp> cd GAM
sftp> put /etc/hostname writetest.txt
sftp> rm writetest.txt
```

Synology chroots the user, so `pwd` at login returns `/` — that is the chroot
root, not the filesystem root. The share appears inside it as `/GAM`. Use
that, never `/volume1/GAM`.

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
BACKUP_SFTP_REMOTE_PATH=/GAM/restic
# Leave the mirror off for now; step 13 turns it on.
BACKUP_MIRROR_PATH=
# Hourly by default. Times are in TZ.
BACKUP_CRON_SCHEDULE=0 * * * *
TZ=Europe/London
```

The `/restic` subdirectory keeps the repo's `data/`, `snapshots/` and `keys/`
from scattering across the share root. `restic init` creates it.

Edit `.env`, not `.env.example` — that stays a template.

## 11. Init and first run

```bash
docker compose build backup
docker compose run --rm backup restic init
docker compose up -d backup
docker compose exec -d backup sh -c '/usr/local/bin/backup.sh >>/proc/1/fd/1 2>>/proc/1/fd/2'
docker logs -f backup
```

`restic init` is one-time and creates the repository on the NAS. The `exec` line
starts the first backup now instead of waiting for the first cron tick, inside the
scheduled container and detached. Ctrl-C on `docker logs -f` stops only the watching,
not the backup.

Inside that container rather than a `docker compose run --rm` one, because the run
lock lives there: a scheduled tick that arrives while this is still going sees it and
skips itself (`previous run still going, skipping`) instead of failing on restic's
repository lock.

### A large first upload

The first run sends the whole library, which over a home line can take most of a day
(restic prints an ETA). Later runs send only what changed, in minutes.

- **If it dies, run the same `exec` line again.** restic saves its index as it goes, so
  what already reached the NAS is not sent twice; the rerun picks up roughly where the
  last one stopped. The dead run's leftover repository lock is cleared automatically.
- **If the home connection is sluggish meanwhile**, cap the upload in `.env`, e.g.
  `BACKUP_LIMIT_UPLOAD_KBPS=5000` (about 40 Mbit/s; KiB/s, applies to restic and the
  mirror), then `docker compose up -d --force-recreate backup` and start it again.
  Remove the cap once the first run is through.
- `BACKUP_SFTP_CONNECTIONS` (default 2) is how many uploads run in parallel. Raise it
  only if the NAS and both lines have room to spare; more connections is what makes a
  saturated home NAS stall.
- With the mirror on, the same run then copies the library a second time into
  `mirror/` — expect the first run to take about twice restic's ETA.

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

## 13. Turn on the plain-file mirror

Nothing to do on the NAS — the account from step 3 can already write to the share. On
the gam host, add to `.env`:

```
BACKUP_MIRROR_PATH=/GAM/mirror
```

The SFTP **chroot** path, like `BACKUP_SFTP_REMOTE_PATH` — not `/volume1/GAM/mirror`.
Keep it beside `restic/`, never inside it: restic owns that directory and `restic check`
would object to strangers in it.

Then recreate the container (environment is read at creation) and force a first run
rather than waiting for the schedule. The first one copies the whole library, so for a
large one run it detached and follow the log:

```bash
docker compose up -d --build --force-recreate backup
docker compose exec -d backup sh -c '/usr/local/bin/backup.sh >>/proc/1/fd/1 2>>/proc/1/fd/2'
docker logs -f backup
```

Every run from then on sends only what changed: new media, the database, and a small
`MIRROR_INFO` file. Ordering is deliberate — media first, then the database — so the
mirrored database never names a file that has not arrived yet.

## 14. Verify the mirror

```bash
docker compose run --rm backup rclone lsf nas:/GAM/mirror
docker compose run --rm backup rclone cat nas:/GAM/mirror/MIRROR_INFO
docker compose run --rm backup rclone size nas:/GAM/mirror/media
du -sh data/media
```

`lsf` should list `MIRROR_INFO`, `db/` and `media/`. `MIRROR_INFO` carries the time of
the last mirror and the database's migration revision. The two sizes should agree.

On the NAS, File Station now shows `GAM/mirror/media/<owner-id>/…` as ordinary files you
can download or play. To run the app against them, carry on with
[`ops/archive/README.md`](../archive/README.md).

What to know about the mirror:

- **It copies deletions.** Removing an asset in GAM removes it from the mirror on the
  next run. To get one back, use restic ("Restoring", below).
- **It is unencrypted on the NAS.** Anyone who can read the share can read the library
  and the database, including asset metadata and transcripts. Provider API keys in the
  database are encrypted with production's `JWT_SECRET_KEY`, which never goes to the NAS.
- **A brief window:** an asset deleted in the seconds between the database snapshot and
  the media sync shows as missing in the archive until the next run.
- **A failed mirror fails the run** (`[backup] FAILED` in `docker logs backup`), but only
  after restic has finished, so a NAS problem with the mirror never costs you a snapshot.

---

## Restoring

Onto the gam host, with the app stopped so nothing writes while files land:

```bash
docker compose stop backend
docker compose run --rm backup restic snapshots                 # pick one, or use latest
docker compose run --rm -v "$PWD/restore:/restore" backup \
  restic restore latest --host gam --target /restore
```

restic restores with full paths: the database lands at `restore/backup/tmp/gam.db` and
media at `restore/backup/src/media/`. Move them into place, then start again:

```bash
mv data/db/gam.db data/db/gam.db.before-restore
rm -f data/db/gam.db-wal data/db/gam.db-shm
cp restore/backup/tmp/gam.db data/db/gam.db
rsync -a restore/backup/src/media/ data/media/
docker compose up -d backend
```

Delete the `-wal` and `-shm` files with the old database, never keep them beside the
restored one — SQLite would replay the old database's log into it.

**From the mirror instead**, when the newest copy is all you need (no restic password, no
restic at all): download `GAM/mirror/db/gam.db` and `GAM/mirror/media/` from the NAS
(File Station, or `sftp`/`rsync` from any machine) into `data/db/` and `data/media/`, the
same way. It is the state as of the last run, so anything deleted before it is not
there.

Test one of these once, before you need it. A backup nobody has restored from is a hope.

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

### `Timeout, server … not responding` partway through a run

Authentication worked — the run got as far as uploading — and then the NAS went quiet
for longer than ssh would wait, usually under the long first upload. ssh now waits five
minutes (`ServerAliveInterval 15` × `ServerAliveCountMax 20` in `entrypoint.sh`) and
restic uses two connections, not five; if it still happens, set
`BACKUP_LIMIT_UPLOAD_KBPS` ("A large first upload", step 11) and rerun. Nothing is
lost: the rerun reuses what was already uploaded.

If it happens on *small* hourly runs too, the link itself is dropping — check the NAS's
**Log Center** for the connection, and whether Gecko Notes' backup over the same link
fails at the same times.

### Read sshd's reasoning directly

Task Scheduler, root. Runs a debug sshd on a spare port that logs why it refused:

```bash
#!/bin/sh
timeout 180 /usr/bin/sshd -ddd -p 2299 -f /etc/ssh/sshd_config \
  > /volume1/GAM/sshd_debug.txt 2>&1
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

An off-site S3-compatible bucket would add geographic redundancy — the NAS and the gam
host are then not the only two copies. Deliberately deferred. Adding it
means a second `restic backup ... -r <bucket-repo>` and its own `restic forget`
in `backup.sh`, with separate credentials. No changes to the NAS setup above.
