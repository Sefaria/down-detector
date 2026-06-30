# CLAUDE.md

Guidance for Claude Code (and any AI agent) working in this repository. Keep this file short and high-signal — see [README.md](README.md) for full documentation.

## What this project is

A Django app that monitors Sefaria's services, confirms outages before alerting, posts Slack alerts, and serves a public status page at status.sefaria.org. Two long-lived processes share one database: a **scheduler** (does all checking/alerting) and a **web** server (renders the status page + admin). See [README.md](README.md#architecture) for the architecture.

## Environment & commands

This project uses a virtual environment at `.venv/`. Prefer invoking it directly rather than relying on shell activation:

```bash
# Tests (always use the test settings via pytest.ini)
.venv/Scripts/python -m pytest tests/ -v
.venv/Scripts/python -m pytest tests/test_state.py -v   # single file

# Dependencies (install only inside the venv)
.venv/Scripts/pip install -r requirements.txt

# Run things locally (manage.py defaults to config.settings.development → SQLite)
.venv/Scripts/python manage.py run_checks --once        # one check cycle, no loop
.venv/Scripts/python manage.py runserver                # status page at :8000
.venv/Scripts/python manage.py migrate
```

Platform note: development happens on Windows (PowerShell). Paths use `.venv/Scripts/`, not `.venv/bin/`.

## How the pieces fit (the non-obvious parts)

- **Settings are split by environment** under `config/settings/`: `development` (default, SQLite), `production` (PostgreSQL via `DATABASE_URL`), `test` (in-memory SQLite, used by `pytest.ini`). Change shared behavior in `base.py`.
- **`MONITORED_SERVICES` in `base.py` is the source of truth** for what gets checked. Adding/removing a service is a config edit — no migration, no code change. The checker, state tracker, status page, and alerter all read from it.
- **The `StateTracker` is a process-global singleton** (`get_state_tracker()` in `state.py`) that lives in the **scheduler** process and rebuilds its state from the DB on first use. It holds the consecutive-failure counts and decides transitions. Tests must call `reset_state_tracker()` between cases that depend on fresh state.
- **The database is the cross-process source of truth for outages.** The admin (web process) can't touch the scheduler's memory, so the tracker maintains the invariant *confirmed-down ⟺ one unresolved `Outage` row* and reconciles against it every cycle (`_reconcile_external_resolution`). This is what lets an operator force-resolve a stuck outage from the admin and have the scheduler absorb it. Preserve that invariant if you touch `state.py`.
- **Four models, four jobs**: `HealthCheck` is the raw per-cycle time-series; `Outage` is one row per confirmed downtime period and is what makes Slack "downtime" accurate; `Message` is an operator-authored incident banner; `Maintenance` is an operator-scheduled maintenance window. The last two are managed in the Django admin.
- **Maintenance windows suppress alerts, not detection.** While a `Maintenance` window is in progress, the scheduler still checks and the tracker still records outages/recoveries, but `run_health_check_cycle` filters out Slack alerts for covered services, and the status page shows them as *Under Maintenance* (`Maintenance.services_under_maintenance()`). A blank `affected_services` covers all services. Note: downtime during a window still counts toward uptime today (not excluded) — operators can force-resolve if needed.
- **Confirmation logic is the heart of the app.** A service is reported DOWN only after `failure_threshold` *consecutive* failed cycles; recovery alerts fire on the first success. The status page (`views.get_service_statuses`) deliberately mirrors this exact logic so the page and Slack never disagree — if you change one, change both.
- **"Degraded" is a page-only signal.** A service that is up but slow (latest response time over `DEGRADED_RESPONSE_MS`, or a per-service `degraded_threshold_ms`) is shown as *Degraded Performance* on the status page, but this never sends a Slack alert and never opens an `Outage` — it's computed in `views.get_service_statuses` only. The Slack path still deals strictly in up/down. So the "page mirrors Slack" rule applies to the down/up decision; degraded is an extra presentation layer on top.
- **A monitor-side failure is never a service outage.** Check results are `up`, `down`, or `error`. `error` means *the monitor itself* couldn't complete the check (its own DB unreachable, a worker crash) — it is inconclusive and is never persisted, never counted toward the failure threshold, never alerted, and never shown on the page (the prior state is preserved). Worker threads in `check_all_services` do pure HTTP and **must not touch the database**; persistence happens once in the scheduler thread as a best-effort bulk write that swallows errors. This prevents the monitor's own Postgres hiccups (e.g. "too many clients") from flapping every service "down" at once — don't reintroduce per-thread DB access or let persistence exceptions propagate.
- **The Linker uses a two-phase async check** (`check_type: "async_two_phase"`): POST → poll the async endpoint for a real `SUCCESS` result. Don't "simplify" it to a status-code check; the depth is intentional.
- **The admin is hardened and enriched.** Login lockout is via `django-axes` (username-keyed; `axes.W006` is intentionally silenced — see `base.py`); reset with `manage.py axes_reset`. A migration creates a least-privilege **`Operators`** group (manage incidents/maintenance, force-resolve outages, read checks; no deletes/user mgmt). The admin index renders a live dashboard via the `status_dashboard` inclusion tag (`monitoring/templatetags/monitoring_admin.py` + `templates/admin/monitoring_index.html`). Maintenance scope is a checkbox `ModelForm` (`MaintenanceAdminForm`) and `Maintenance.clean()` rejects bad windows. Tests set `AXES_ENABLED=False`; with axes enabled, `authenticate()` requires a `request`, so use `client.force_login()` not `client.login()` in any axes-enabled context.
- **The status page is mostly self-updating.** It polls a cached JSON endpoint `GET /api/status/` (`views.status_api`) every ~20s and patches the banner + per-service rows in place; a 5-minute full reload refreshes incidents, the verse, and the uptime/sparkline sections. Other routes (in `monitoring/urls.py`): `/healthz` (container liveness, no DB), `/history.rss` + `/history.atom` (incident feeds, `monitoring/feeds.py`), `/robots.txt`, `/sitemap.xml`. The 90-day uptime bars come from `views.get_uptime_history` (computed from `Outage`, not raw checks); the per-service latency sparkline from `views.get_response_time_sparklines` (inline SVG, no JS lib).
- **Deploy is driven by `scripts/web-entrypoint.sh`.** The web container is the single migrator: on every deploy it **waits for the DB** to accept connections, then runs `migrate` (fatal) → `collectstatic` (non-fatal) → `check --deploy` (informational) → `gunicorn`. The scheduler/cleanup containers reuse the image, override the command, and never migrate. **Compose `depends_on` uses `service_started`, not `service_healthy`** — gating `up` on healthchecks made `docker compose up` block and could fail the whole deploy, so we order containers but let the entrypoint wait for the DB itself. The container `HEALTHCHECK` hits `/healthz`, and production always appends `localhost`/`127.0.0.1` to `ALLOWED_HOSTS` so that loopback probe passes regardless of operator config — don't remove that, it's what keeps deploys from failing as "web unhealthy".

## Working in this codebase

- Match the existing style: type hints, module-level docstrings, `logging` (not print), `getattr(settings, ...)` for config with defaults.
- Prefer adding tests alongside changes — every service module has a matching `tests/test_*.py`. Mock outbound HTTP (`httpx`) and Slack (`WebhookClient`); tests make no real network calls. Use `time-machine` for time-dependent logic.
- When you change a default or a service URL, update it in **`base.py`, `.env.example`, and `README.md`** together — these have drifted before.

## Git commits

- Do **not** add `Co-Authored-By` lines to commit messages.
- `CLAUDE.md` **is** tracked and committed (only the `.claude/` directory is git-ignored), so it reaches anyone who clones the repo — keep it current as a handoff doc.
