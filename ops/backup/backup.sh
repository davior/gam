#!/bin/sh
# One backup run: a WAL-safe whole-database snapshot, a restic push of that
# snapshot plus the media tree (restic sends only what the destination
# doesn't already have), then retention pruning.
set -eu
trap 'echo "[backup] FAILED (exit $?)" >&2' ERR

echo "[backup] $(date -u +%Y-%m-%dT%H:%M:%SZ) starting"

mkdir -p /backup/tmp
rm -f /backup/tmp/gam.db

echo "[backup] taking WAL-safe sqlite snapshot"
sqlite3 /backup/src/db/gam.db ".backup '/backup/tmp/gam.db'"

echo "[backup] running restic backup"
# --host pins every run to the same identity so `restic forget` groups them
# together for retention purposes, instead of each run's random container
# hostname fragmenting the snapshot history into groups of one.
restic backup /backup/tmp/gam.db /backup/src/media \
  --host gam \
  --tag scheduled

echo "[backup] pruning per retention policy"
restic forget \
  --keep-hourly "${BACKUP_KEEP_HOURLY:-48}" \
  --keep-daily "${BACKUP_KEEP_DAILY:-14}" \
  --keep-weekly "${BACKUP_KEEP_WEEKLY:-8}" \
  --prune

echo "[backup] done"
