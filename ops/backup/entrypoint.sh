#!/bin/sh
# Builds the SSH config and cron schedule from env vars on every start, then
# either execs the command it was given (so `docker compose run --rm backup
# restic ...` gets the same ssh/env setup as a scheduled run) or falls
# through to running the schedule in the foreground.
set -eu

# ServerAlive: give up after five minutes of silence, not ninety seconds. A NAS busy
# writing an upload that runs for hours can go quiet for over a minute and still be
# there; at 30s x 3 the first real run died that way, and restic cannot reconnect once
# its ssh has exited — the whole run is lost, not one file.
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
    TCPKeepAlive yes
    ServerAliveInterval 15
    ServerAliveCountMax 20
EOF
chmod 700 /root/.ssh
chmod 600 /root/.ssh/config

# The repository and the mirror's rclone remote. In a file of their own because
# backup.sh needs them too, and is not always started from here (see env.sh).
. /usr/local/lib/backup-env.sh

SCHEDULE="${BACKUP_CRON_SCHEDULE:-0 * * * *}"
echo "${SCHEDULE} /usr/local/bin/backup.sh >>/proc/1/fd/1 2>>/proc/1/fd/2" > /etc/crontabs/root

echo "[entrypoint] schedule: ${SCHEDULE}"
echo "[entrypoint] repository: ${RESTIC_REPOSITORY}"
echo "[entrypoint] mirror: ${BACKUP_MIRROR_PATH:-off}"

if [ "$#" -gt 0 ]; then
  exec "$@"
else
  exec crond -f -l 8
fi
