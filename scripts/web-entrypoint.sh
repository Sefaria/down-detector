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

# Wait for the database to accept connections before migrating. The compose
# stack no longer gates `up` on the db healthcheck (that made `docker compose
# up` block and could fail the whole deploy), so the web container waits for
# the DB itself here. This keeps `up -d` fast while still migrating safely.
echo "[release] Waiting for the database to accept connections..."
python <<'PY'
import os, time
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
django.setup()
from django.db import connections
from django.db.utils import Error

for attempt in range(60):
    try:
        connections["default"].cursor()
        print("[release] Database is ready.")
        break
    except Error as exc:
        if attempt == 0:
            print(f"[release] Database not ready yet: {exc}")
        time.sleep(1)
else:
    print("[release] Database still not ready after 60s; continuing (migrate will surface it).")
PY

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
