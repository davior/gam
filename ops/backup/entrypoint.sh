#!/bin/sh
# Builds the SSH config and cron schedule from env vars on every start, then
# either execs the command it was given (so `docker compose run --rm backup
# restic ...` gets the same ssh/env setup as a scheduled run) or falls
# through to running the schedule in the foreground.
set -eu

mkdir -p /root/.ssh
cat > /root/.ssh/config <<EOF
Host synology-backup
    HostName ${BACKUP_SFTP_HOST}
    Port ${BACKUP_SFTP_PORT}
    User ${BACKUP_SFTP_USER}
    IdentityFile /root/.ssh/id_backup
    IdentitiesOnly yes
    UserKnownHostsFile /root/.ssh/known_hosts
    StrictHostKeyChecking yes
    BatchMode yes
    PasswordAuthentication no
    ConnectTimeout 15
    ServerAliveInterval 30
    ServerAliveCountMax 3
EOF
chmod 700 /root/.ssh
chmod 600 /root/.ssh/config

export RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-sftp:synology-backup:${BACKUP_SFTP_REMOTE_PATH}}"

SCHEDULE="${BACKUP_CRON_SCHEDULE:-0 * * * *}"
echo "${SCHEDULE} /usr/local/bin/backup.sh >>/proc/1/fd/1 2>>/proc/1/fd/2" > /etc/crontabs/root

echo "[entrypoint] schedule: ${SCHEDULE}"
echo "[entrypoint] repository: ${RESTIC_REPOSITORY}"

if [ "$#" -gt 0 ]; then
  exec "$@"
else
  exec crond -f -l 8
fi
