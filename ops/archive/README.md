# Read-only archive: GAM on the Synology

A copy of GAM that runs in Container Manager on the NAS against the plain-file mirror
the backup sidecar keeps there. It shows your library exactly as of the last mirror
run, plays every video, and opens every image and document. It changes nothing: the
database is opened read-only from a read-only mount, every save is refused, and no
background job runs.

It needs nothing from production. It doesn't use Notes, the gam host or the internet,
which is the point: it is how you get at the library on the day those are down.

What it is not:

- **Not signed in.** Notes only redirects back to `*.geckopico.com`, and an archive that
  needed Notes would be useless on exactly the day you want it. So it shows one user's
  library, `READ_ONLY_USER`'s, to **whoever can reach its port**. Keep it on your LAN or
  Tailscale. Never port-forward it.
- **Not semantic search.** Provider API keys are encrypted with production's
  `JWT_SECRET_KEY`, which deliberately does not go to the NAS (it would let anyone who
  can read the NAS mint a Notes session). Search falls back to text matching, and
  generation, transcription and the rest are off anyway.
- **Not live.** It is as fresh as the last mirror run — hourly by default.

---

## Before you start

- The mirror works: steps 1–14 of [`ops/backup/README.md`](../backup/README.md) are done
  and step 14 shows `MIRROR_INFO`, `db/` and `media/` under `GAM/mirror`.
- The NAS runs **DSM 7.2 or later** and its model supports **Container Manager**
  (Package Center shows it; most Plus-series models and a few ARM64 ones do). Building
  the images needs about 3 GB free on the volume holding the `docker` share.

## 1. Install Container Manager

**Package Center** → search **Container Manager** → Install. It creates a `docker`
shared folder, which is where the app's code goes.

## 2. Put the code on the NAS

The archive must run the **same version** as production, or near it. Code older than
the mirrored database cannot read the newer schema. Find production's commit on the gam
host:

```bash
cd /opt/gam && git rev-parse HEAD
```

On GitHub, signed in (the repository is private), download that commit as a ZIP:
`https://github.com/davior/gam/archive/<commit>.zip`.

In **File Station**:

1. Open the `docker` share, then **Upload** the ZIP.
2. Right-click it → **Extract** → **Extract here**. You get a folder like
   `gam-<commit>`. Rename it to `gam`.
3. Check that `docker/gam/ops/archive/docker-compose.yml` exists.

No Git, SSH or shell on the NAS is needed.

## 3. Find whose library to show

Your Notes **username** works as `READ_ONLY_USER`, and so does your GAM user id. The id
is the safer one, since a username can be renamed. To read it on the gam host:

```bash
cd /opt/gam
docker compose exec backend python -c \
  "import sqlite3; print(sqlite3.connect('/app/data/db/gam.db').execute('select id, username from user').fetchall())"
```

## 4. Create `ops/archive/.env`

On your computer, generate a **new** secret. Do not reuse production's:

```bash
openssl rand -hex 32
```

(No `openssl`? Any 64 random hex characters will do; a password manager can generate
them.)

Make a text file named exactly `.env` with this content, filling in your values:

```
GAM_MIRROR_DIR=/volume1/GAM/mirror
READ_ONLY_USER=<id or username from step 3>
JWT_SECRET_KEY=<the new secret>
ARCHIVE_PORT=18083
TZ=Europe/London
```

`GAM_MIRROR_DIR` is the NAS's own path, starting `/volume1/` (File Station →
right-click the share → **Properties** shows where it lives). It is *not* the chroot
path the backup uses. If GAM's backup shares Gecko Notes' NAS account (the backup
README's "Already backing up Gecko Notes" section), the mirror is in Notes' share:
`GAM_MIRROR_DIR=/volume1/GeckoNotes/gam/mirror`.

Upload the file to `docker/gam/ops/archive/` with File Station. On Windows, Notepad
saves it as `.env.txt` unless you pick **Save as type: All files**. Check the name in
File Station afterwards, and rename it if needed.

## 5. Create the project

**Container Manager → Project → Create**:

| Field  | Value |
| ------ | ----- |
| Project name | `gam-archive` |
| Path | `/docker/gam/ops/archive` |
| Source | **Use existing docker-compose.yml** |

Pick that folder, not `docker/gam` itself. The `docker-compose.yml` at the repository
root is production's, and it includes the backup sidecar.

Skip the web portal (Web Station) page if it is offered → **Done**. Container Manager
builds both images, which takes 10–30 minutes on a NAS CPU the first time, then starts
them. If the build stops with `set GAM_MIRROR_DIR in ops/archive/.env` (or one of the
other two), the `.env` was not found or that line is empty. Go back to step 4.

## 6. Open it

`http://<nas-lan-ip>:18083`

You should see your library with an amber **Read-only archive copy** bar under the
header, no sign-in, and no sign-out button. Play a video, and try to rename something:
the rename is refused with "This is a read-only archive copy".

An **empty library** means `READ_ONLY_USER` matches nobody in the database. Re-check
step 3, edit `.env`, and recreate the project (step 8).

## 7. Keep it off the internet

**Control Panel → Security → Firewall** → edit the profile → add, above any allow-all
rule:

- Allow port `18083` from your LAN subnet (e.g. `192.168.1.0/24`).
- Allow port `18083` from `100.64.0.0/10` if you use Tailscale.
- Deny port `18083` from everything else.

And on the router: **no** port-forward for 18083.

## 8. Day to day

**It refreshes on its own.** Each mirror run replaces `gam.db` with a rename, and the
archive opens a fresh connection per request. The next page load shows the new state,
with no restart needed. `GAM/mirror/MIRROR_INFO` says when the last run was.

**After updating production**, update the archive the same way it was installed. Download the
new commit's ZIP, extract it, and replace `docker/gam` with it, keeping your
`ops/archive/.env`. Then **Container Manager → Project → gam-archive → Action → Stop**,
then **Build**, then **Start**. If the archive's logs (**Container → gam-archive-backend
→ Log**) say the database "is at migration …, which this checkout does not contain",
this is the step you missed.

**After changing `.env`**, use Stop → Build → Start as well. A plain restart keeps the
old environment.

## Troubleshooting

| Symptom | Cause |
| ------- | ----- |
| Build fails at `set … in ops/archive/.env` | `.env` missing, misnamed (`.env.txt`), or that line empty — step 4 |
| Page loads, library empty | `READ_ONLY_USER` matches no user — step 3 |
| "Could not reach the asset library" | Backend container not running or unhealthy. Look at its log; a wrong `GAM_MIRROR_DIR` shows as `unable to open database file` |
| `unable to open database file` | `GAM_MIRROR_DIR` points at the wrong place, or the mirror has not run yet. `ls` the path in File Station: it must hold `db/gam.db` |
| Images show, videos never play | Usually a browser blocking plain-HTTP media on a LAN address. Try another browser, or reach it over Tailscale's MagicDNS name |
| Library hours or days old | The mirror has stopped. Check `docker logs backup` on the gam host for `[backup] FAILED` |
