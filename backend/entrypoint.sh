#!/bin/sh
# Migrations run before the app accepts traffic, not from inside it.
#
# Doing it here rather than in a startup hook means a failed migration stops the
# container instead of leaving a running app serving a schema it does not match —
# which the orchestrator can see, and a half-migrated app cannot tell you.
set -e

# Never against a read-only archive. Its database is a mirror of production's: migrating
# it would fail on the read-only mount, and if it did not, the next mirror run would put
# production's schema back over it.
case "$(echo "${READ_ONLY:-}" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on)
    echo "Read-only mode: skipping migrations."
    ;;
  *)
    echo "Running database migrations..."
    alembic upgrade head
    ;;
esac

echo "Starting API..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 "$@"
