#!/bin/sh
# Container start: apply migrations, load targets/known analogs the first time, then serve.
# Set RUN_MIGRATIONS=0 to skip the first two steps (e.g. if you run them as a Cloud Run job).
set -e
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
  python manage.py migrate --noinput
  python manage.py load_export --only-new
fi
exec gunicorn server.wsgi:application \
  --bind "0.0.0.0:${PORT:-8080}" \
  --workers "${GUNICORN_WORKERS:-2}" --threads "${GUNICORN_THREADS:-4}" \
  --timeout 55 --access-logfile -
