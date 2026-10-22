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

# The plain-file mirror's remote, configured through rclone's environment variables so
# there is no config file to mount or keep in step. Same account, key and pinned host key
# as restic — no NAS change is needed to turn the mirror on.
#
# SHELL_TYPE=none and DISABLE_HASHCHECK: DSM gives the backup account SFTP and no shell,
# so rclone cannot run md5sum remotely, and without these it tries and logs a failure
# per file. Size + modtime is a sound comparison here because media names are
# write-once UUIDs: a file with a given name never changes.
# /dev/null: everything is in the environment, and without it rclone logs that it found
# no config file on every single invocation.
export RCLONE_CONFIG=/dev/null
export RCLONE_CONFIG_NAS_TYPE=sftp
export RCLONE_CONFIG_NAS_HOST="${BACKUP_SFTP_HOST}"
export RCLONE_CONFIG_NAS_PORT="${BACKUP_SFTP_PORT}"
export RCLONE_CONFIG_NAS_USER="${BACKUP_SFTP_USER}"
export RCLONE_CONFIG_NAS_KEY_FILE=/root/.ssh/id_backup
export RCLONE_CONFIG_NAS_KNOWN_HOSTS_FILE=/root/.ssh/known_hosts
export RCLONE_CONFIG_NAS_SHELL_TYPE=none
export RCLONE_CONFIG_NAS_DISABLE_HASHCHECK=true

export RESTIC_REPOSITORY="${RESTIC_REPOSITORY:-sftp:synology-backup:${BACKUP_SFTP_REMOTE_PATH}}"

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
