#!/bin/sh
# One backup run: a WAL-safe whole-database snapshot, a restic push of that
# snapshot plus the media tree (restic sends only what the destination
# doesn't already have), then retention pruning. Then, if BACKUP_MIRROR_PATH is set, a
# plain-file mirror of the same two things that a read-only copy of the app can run
# from (docs/deployment.md, "Read-only archive").
set -eu
trap 'echo "[backup] FAILED (exit $?)" >&2' ERR

mkdir -p /backup/tmp

# One run at a time. A first upload of a large library takes most of a day, and the
# schedule is hourly: without this, each tick would start a second run that fails on
# restic's repository lock and reports FAILED, and the real one would be buried. The
# lock lives in the container's tmpfs, so it is held exactly as long as this process
# is — which is also why a manual run goes through `docker compose exec`, not `run`.
exec 9>/backup/tmp/backup.lock
if ! flock -n 9; then
  echo "[backup] $(date -u +%Y-%m-%dT%H:%M:%SZ) previous run still going, skipping"
  exit 0
fi

echo "[backup] $(date -u +%Y-%m-%dT%H:%M:%SZ) starting"

# Fewer than restic's default of 5 parallel SFTP connections: a home NAS on a home
# line, saturated for hours, stalls long enough for ssh to give up. Numbers only, so
# leaving these unquoted below to split into flags is safe.
CONNECTIONS="${BACKUP_SFTP_CONNECTIONS:-2}"
RESTIC_LIMIT=""
RCLONE_LIMIT=""
if [ -n "${BACKUP_LIMIT_UPLOAD_KBPS:-}" ]; then
  RESTIC_LIMIT="--limit-upload ${BACKUP_LIMIT_UPLOAD_KBPS}"
  RCLONE_LIMIT="--bwlimit ${BACKUP_LIMIT_UPLOAD_KBPS}k"
fi

# A run that died — the connection dropped, the container was recreated — leaves its
# lock in the repository, and every later run would refuse to start until a person
# cleared it. Without --remove-all this removes only locks restic judges stale, never
# one a live process holds (Notes' repository is a different one, and untouched).
restic unlock -o sftp.connections="${CONNECTIONS}"

rm -f /backup/tmp/gam.db

echo "[backup] taking WAL-safe sqlite snapshot"
sqlite3 /backup/src/db/gam.db ".backup '/backup/tmp/gam.db'"

echo "[backup] running restic backup"
# --host pins every run to the same identity so `restic forget` groups them
# together for retention purposes, instead of each run's random container
# hostname fragmenting the snapshot history into groups of one.
restic backup /backup/tmp/gam.db /backup/src/media \
  --host gam \
  --tag scheduled \
  -o sftp.connections="${CONNECTIONS}" \
  ${RESTIC_LIMIT}

echo "[backup] pruning per retention policy"
restic forget \
  --keep-hourly "${BACKUP_KEEP_HOURLY:-48}" \
  --keep-daily "${BACKUP_KEEP_DAILY:-14}" \
  --keep-weekly "${BACKUP_KEEP_WEEKLY:-8}" \
  --prune \
  -o sftp.connections="${CONNECTIONS}" \
  ${RESTIC_LIMIT}

if [ -n "${BACKUP_MIRROR_PATH:-}" ]; then
  MIRROR="nas:${BACKUP_MIRROR_PATH%/}"

  # A copy, not the restic snapshot itself, which stays byte-for-byte what was taken.
  # Rollback-journal mode because the archive opens it `mode=ro` from a read-only
  # mount, and a WAL-flagged file would need a writable -shm beside it there.
  rm -f /backup/tmp/mirror.db
  cp /backup/tmp/gam.db /backup/tmp/mirror.db
  sqlite3 /backup/tmp/mirror.db "PRAGMA journal_mode=DELETE;" >/dev/null

  # Media before the database, so the mirrored database never names a file that has
  # not arrived. `sync`, so a removed asset leaves the mirror too — restic is what
  # keeps history; this is a working copy. rclone's default --delete-after skips
  # deletions entirely when anything in the run failed. The one gap: an asset deleted
  # in the seconds between the snapshot and this sync can show as missing in the
  # archive until the next run.
  echo "[backup] mirroring media to ${MIRROR}/media"
  rclone sync /backup/src/media "${MIRROR}/media" \
    --exclude '*.partial' \
    --transfers "${CONNECTIONS}" \
    ${RCLONE_LIMIT} \
    -v

  # Uploaded under a temporary name and renamed into place by rclone, so an archive
  # reading the mirror never opens half a database.
  echo "[backup] mirroring database to ${MIRROR}/db/gam.db"
  rclone copyto /backup/tmp/mirror.db "${MIRROR}/db/gam.db" ${RCLONE_LIMIT}

  # What the archive's operator checks to know which code version to run: an archive
  # older than the schema it is given cannot read it.
  {
    echo "mirrored_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "alembic_revision=$(sqlite3 /backup/tmp/mirror.db 'SELECT version_num FROM alembic_version' 2>/dev/null || true)"
  } > /backup/tmp/MIRROR_INFO
  rclone copyto /backup/tmp/MIRROR_INFO "${MIRROR}/MIRROR_INFO"

  rm -f /backup/tmp/mirror.db
fi

echo "[backup] done"
