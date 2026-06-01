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
- **The `StateTracker` is a process-global singleton** (`get_state_tracker()` in `state.py`) that rebuilds its state from the DB on first use. It holds the consecutive-failure counts and decides transitions. Tests must call `reset_state_tracker()` between cases that depend on fresh state.
- **Three models, three jobs**: `HealthCheck` is the raw per-cycle time-series; `Outage` is one row per confirmed downtime period and is what makes Slack "downtime" accurate; `Message` is an operator-authored incident banner shown on the status page (managed in the Django admin).
- **Confirmation logic is the heart of the app.** A service is reported DOWN only after `failure_threshold` *consecutive* failed cycles; recovery alerts fire on the first success. The status page (`views.get_service_statuses`) deliberately mirrors this exact logic so the page and Slack never disagree — if you change one, change both.
- **The Linker uses a two-phase async check** (`check_type: "async_two_phase"`): POST → poll the async endpoint for a real `SUCCESS` result. Don't "simplify" it to a status-code check; the depth is intentional.

## Working in this codebase

- Match the existing style: type hints, module-level docstrings, `logging` (not print), `getattr(settings, ...)` for config with defaults.
- Prefer adding tests alongside changes — every service module has a matching `tests/test_*.py`. Mock outbound HTTP (`httpx`) and Slack (`WebhookClient`); tests make no real network calls. Use `time-machine` for time-dependent logic.
- When you change a default or a service URL, update it in **`base.py`, `.env.example`, and `README.md`** together — these have drifted before.

## Git commits

- Do **not** add `Co-Authored-By` lines to commit messages.
- This repo currently git-ignores `CLAUDE.md` and `.claude/` (see `.gitignore`). If this file should reach other engineers, remove the `CLAUDE.md` line from `.gitignore` and commit it.
