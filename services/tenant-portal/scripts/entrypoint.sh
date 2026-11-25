#!/bin/sh
# Container entrypoint. Migrations run only when RUN_MIGRATIONS=true (local
# compose). In Kubernetes, run them from a Job or init container instead.
set -e

if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
    python manage.py migrate --noinput
    python manage.py createcachetable
fi

exec "$@"
#run migrations and start the server