# Settings derived from the container's environment, for every way backup.sh runs.
#
# Sourced, not executed: by entrypoint.sh at start, and by backup.sh itself. The second
# is the load-bearing one. Exports made in the entrypoint reach the cron jobs crond
# forks and `docker compose run` commands, but not `docker compose exec`, which starts a
# fresh process with only the container's configured environment — so a backup started
# that way had no repository and failed with "Please specify repository location".

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

