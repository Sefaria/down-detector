#!/bin/sh
# Release + start entrypoint for the web container.
#
# Runs the steps that must happen on every deploy so the running app is
# always consistent with the code being deployed, then hands off to gunicorn.
# This is the single migrator: the scheduler and cleanup containers do NOT
# migrate, so schema changes are applied exactly once.
#
# Each step is idempotent and safe to re-run.
set -eu

# Migrations are fatal: do not serve against a schema that doesn't match the
# code being deployed.
echo "[release] Applying database migrations..."
python manage.py migrate --noinput

# Static files are already collected at image build; re-running keeps them in
# sync but must never block the server from coming up (it would make the
# container fail its healthcheck and fail the whole deploy). Non-fatal.
echo "[release] Collecting static files..."
python manage.py collectstatic --noinput || echo "[release] collectstatic failed; continuing"

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
