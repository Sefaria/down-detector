#!/bin/sh
# Release + start entrypoint for the web container.
#
# Runs the steps that must happen on every deploy so the running app is
# always consistent with the code being deployed, then hands off to gunicorn.
# This is the single migrator: the scheduler and cleanup containers do NOT
# migrate (they wait for the web service to become healthy first), so schema
# changes are applied exactly once.
#
# Each step is idempotent and safe to re-run.
set -eu

echo "[release] Applying database migrations..."
python manage.py migrate --noinput

echo "[release] Collecting static files..."
python manage.py collectstatic --noinput

# Surface production misconfiguration (insecure settings, missing SECRET_KEY,
# etc.) in the deploy logs. Informational only: warnings must not block a
# deploy, so we never let this fail the container.
echo "[release] Running deploy checks (informational)..."
python manage.py check --deploy || true

echo "[release] Starting gunicorn..."
exec gunicorn \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${GUNICORN_WORKERS:-2}" \
    --threads "${GUNICORN_THREADS:-2}" \
    --access-logfile - \
    --error-logfile - \
    config.wsgi:application
