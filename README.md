# Sefaria Status Monitor

Real-time uptime monitoring and a public status page for Sefaria's critical services — live at **[status.sefaria.org](https://status.sefaria.org)**.

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://python.org)
[![Django 5.2](https://img.shields.io/badge/django-5.2-green.svg)](https://djangoproject.com)
[![Tests](https://img.shields.io/badge/tests-149%20passing-brightgreen.svg)](#testing)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A small, self-contained Django application that checks Sefaria's services on a fixed interval, records every result, confirms outages before alerting (to filter out brief blips), posts rich [Slack](https://slack.com) notifications when a service goes down or recovers, and renders a public, SEO-optimized status page.

---

## Table of Contents

- [Why this exists](#why-this-exists)
- [What it monitors](#what-it-monitors)
- [How it works](#how-it-works)
- [Architecture](#architecture)
- [Data model](#data-model)
- [Quick start (local development)](#quick-start-local-development)
- [Configuration](#configuration)
- [Management commands](#management-commands)
- [The status page](#the-status-page)
- [Admin access & security](#admin-access--security)
- [Deployment](#deployment)
- [Testing](#testing)
- [Project structure](#project-structure)
- [Operations & runbook](#operations--runbook)
- [License](#license)

---

## Why this exists

Sefaria runs several public services (the main site, an MCP server, an AI chatbot, and the Linker API). When one degrades, the team needs to (a) find out fast, and (b) give users a single trustworthy place to check. This project does both:

- **Fast, accurate alerts** to a Slack channel, with the *real* outage start time and total downtime on recovery.
- **A public status page** that anyone can check during an incident, so support volume drops and users aren't left guessing.

The design goal is **low false-positive rate**: a single failed request never pages anyone. A service must fail *N* consecutive check cycles (configurable per service) before it is reported as down, both in Slack and on the status page.

## What it monitors

Services are declared in [`config/settings/base.py`](config/settings/base.py) (`MONITORED_SERVICES`). Each URL can be overridden via an environment variable.

| Service | Check | Method | Expects | Failure threshold | Env override |
|---|---|---|---|---|---|
| **sefaria.org** | `…/healthz` | GET | `200` | 2 cycles | `SEFARIA_HEALTH_URL` |
| **MCP Server** | `mcp.sefaria.org/healthz` | GET | `200` | 4 cycles | `MCP_HEALTH_URL` |
| **AI Chatbot** | `chat.sefaria.org/api/health` | GET | `200` | 4 cycles | `AI_CHATBOT_HEALTH_URL` |
| **Linker** | `…/api/find-refs` | POST | `202` + async result | 3 cycles | `LINKER_HEALTH_URL` |

The **Linker** uses a two-phase async check (see below) and has a higher threshold because it is the noisiest service. **MCP Server** and **AI Chatbot** use a higher threshold (4) because their origins are restarted on a daily schedule (~07:2x UTC), briefly returning Cloudflare `521`; requiring four consecutive failures absorbs that routine restart while still catching a sustained outage.

## How it works

A single check **cycle** runs every `HEALTH_CHECK_INTERVAL` seconds (default 60):

1. **Check** — All services are checked **in parallel** ([`ThreadPoolExecutor`](https://docs.python.org/3/library/concurrent.futures.html)) so one slow/down service never blocks the others. Each request is retried up to `HEALTH_CHECK_RETRIES` times with `HEALTH_CHECK_RETRY_DELAY` seconds between attempts. **Worker threads do pure HTTP and never touch the database** — see *Conclusive vs. inconclusive results* below.
2. **Persist** — Conclusive results are written to the `HealthCheck` table (status, HTTP code, response time, error) in a single bulk write, in the scheduler thread. Persistence is best-effort: a failure to write to the monitor's own DB is logged and never turned into a fake outage.
3. **Detect transitions** — A `StateTracker` compares each result against the last known state and decides whether a *reportable* transition occurred.
4. **Alert** — On a confirmed `went_down` or `recovered` transition, a Slack Block Kit message is sent.

### Conclusive vs. inconclusive results

A check result is one of three kinds:

- **`up`** — the target answered correctly.
- **`down`** — the target answered incorrectly or was unreachable. This is a real, reportable outage and counts toward the failure threshold.
- **`error`** — the *monitor itself* couldn't complete the check (e.g. its own database was unreachable, or a worker crashed). This is **inconclusive**: it says nothing about the target, so it is never persisted, never counted toward the threshold, never alerted on, and never shown on the status page. The last known real state is preserved.

This distinction is what stops a hiccup in the monitor's *own* infrastructure from flapping every service "down" at once. Worker threads also never open a database connection, which removes the per-cycle connection leak that previously exhausted Postgres ("too many clients") and produced exactly those phantom outages.

### Confirmation logic (the important part)

- A service is only reported **DOWN** after it fails `failure_threshold` **consecutive** cycles. The first failures in a streak are counted but stay silent.
- When a service is confirmed down, an **`Outage`** record is opened with the timestamp of the *first* failure in the streak — so the "Since" time in Slack and the measured downtime are accurate, not the time the threshold was crossed.
- **Recovery fires immediately** on the first successful check after a confirmed outage. The open `Outage` is closed (`end_time`, `resolved=True`) and its duration drives the "Downtime" field in the recovery alert.
- A blip that self-resolves before hitting the threshold produces **no alert** and no `Outage`.

The tracker is a process-global singleton ([`get_state_tracker()`](monitoring/services/state.py)) that rebuilds its in-memory state from the database on first use, so it survives process restarts without re-alerting on an already-known outage.

### Two-phase async check (Linker)

A plain `202 Accepted` from the Linker only means "task queued" — it doesn't prove the background worker, ML model, or ElasticSearch actually work. The Linker check therefore:

1. **Phase 1** — POSTs a real reference (`"Job 1:1"`), expects `202` and extracts a `task_id`.
2. **Phase 2** — Polls `…/api/async/<task_id>` until the task reaches `SUCCESS` **with a non-empty result**. A `FAILURE` state, an empty result, or a polling timeout all count as down.

This catches end-to-end failures a shallow check would miss.

## Architecture

The system runs as **two long-lived processes** plus an on-demand maintenance job, all from the same image:

```
                          ┌──────────────────────────────────────────┐
                          │              PostgreSQL                   │
                          │   HealthCheck · Outage · Message tables   │
                          └──────────────────────────────────────────┘
                              ▲                              ▲
              writes results  │                              │  reads latest state
                              │                              │
┌─────────────────────────────────────────┐   ┌──────────────────────────────────────┐
│   SCHEDULER process (run_checks)         │   │   WEB process (gunicorn)             │
│                                          │   │                                      │
│   APScheduler                            │   │   Django                             │
│    ├─ every 60s → health check cycle     │   │    ├─ GET /         → status page    │
│    │     check_all_services (parallel)   │   │    ├─ GET /robots.txt                │
│    │     → StateTracker (UP/DOWN detect) │   │    └─ GET /sitemap.xml               │
│    │     → Slack alerter (Block Kit)     │   │    └─ GET /admin/   → incident mgmt  │
│    └─ daily 03:00 UTC → cleanup old rows  │   │                                      │
└─────────────────────────────────────────┘   └──────────────────────────────────────┘
                              │
                              ▼
                       ┌─────────────┐
                       │    Slack    │  🔴 down / 🟢 recovered
                       └─────────────┘
```

- The **scheduler** does all the checking, alerting, and daily cleanup. It owns the `StateTracker`.
- The **web** process only reads from the database to render the status page and serve the Django admin (used by operators to post incident messages). It never performs checks.
- Both connect to the same PostgreSQL database, which is the single source of truth.

Key modules:

| File | Responsibility |
|---|---|
| [`monitoring/services/checker.py`](monitoring/services/checker.py) | Performs HTTP checks (standard + async two-phase), retries, parallel execution |
| [`monitoring/services/state.py`](monitoring/services/state.py) | `StateTracker` — consecutive-failure logic, transition detection, `Outage` lifecycle |
| [`monitoring/services/alerter.py`](monitoring/services/alerter.py) | Builds and sends Slack Block Kit alerts |
| [`monitoring/services/scheduler.py`](monitoring/services/scheduler.py) | APScheduler wiring; the `run_health_check_cycle` and cleanup jobs |
| [`monitoring/views.py`](monitoring/views.py) | Status page, status-aware quotes, robots.txt, sitemap.xml |
| [`monitoring/models.py`](monitoring/models.py) | `HealthCheck`, `Outage`, `Message` |

## Data model

| Model | Purpose | Notes |
|---|---|---|
| **`HealthCheck`** | One row per service per check cycle | The raw time-series. Pruned after `HEALTH_CHECK_RETENTION_DAYS`. |
| **`Outage`** | One row per confirmed downtime period | `start_time` = first failure in the streak; closed on recovery. Drives accurate Slack downtime. Viewable in the admin and can be **force-resolved** if stuck (see [runbook](#operations--runbook)). |
| **`Message`** | An operator-authored incident note shown on the status page | Severity `high` / `medium` / `resolved`; managed in Django admin. |
| **`Maintenance`** | An operator-scheduled maintenance window | Title, description, affected services (blank = all), start/end, active. While in progress, covered services show "Under Maintenance" and their Slack alerts are suppressed. Managed in Django admin. |

## Quick start (local development)

> **Prerequisites:** Python 3.12. Local development uses SQLite — no PostgreSQL or Docker required.

```bash
git clone <repository-url>
cd "Sefaria Down Detector"

# Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate            # Windows (PowerShell/CMD)
# source .venv/bin/activate       # macOS / Linux

# Install dependencies
pip install -r requirements.txt

# Initialize the database and an admin user
python manage.py migrate
python manage.py createsuperuser

# Terminal 1 — run the web/status page (http://localhost:8000)
python manage.py runserver

# Terminal 2 — run one check cycle and exit (no Slack needed)
python manage.py run_checks --once
```

`manage.py` defaults to **`config.settings.development`** (SQLite, `DEBUG=True`). To run the full scheduler loop locally, use `python manage.py run_checks` (Ctrl+C to stop).

> Without a `SLACK_WEBHOOK_URL`, checks still run and persist — alerts are simply skipped with a log line. This is the normal local-dev setup.

## Configuration

Settings are split by environment under [`config/settings/`](config/settings/) and selected with `DJANGO_SETTINGS_MODULE`:

| Module | Used by | Database | Debug |
|---|---|---|---|
| `config.settings.development` | `manage.py` default | SQLite | `True` |
| `config.settings.production` | Docker / Coolify | PostgreSQL (`DATABASE_URL`) | `False` |
| `config.settings.test` | pytest (`pytest.ini`) | in-memory SQLite | `False` |

### Environment variables

Copy [`.env.example`](.env.example) to `.env` and edit. All monitoring tunables read from the environment via [`django-environ`](https://django-environ.readthedocs.io/).

| Variable | Description | Default |
|---|---|---|
| `SECRET_KEY` | Django secret key | dev placeholder (**set in prod**) |
| `DEBUG` | Enable debug mode | `False` |
| `ALLOWED_HOSTS` | Comma-separated hosts | `status.sefaria.org` (prod) |
| `DATABASE_URL` | PostgreSQL connection URL (prod) | SQLite (dev) |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook; **alerts are skipped if empty** | `""` |
| `SLACK_CHANNEL` | Informational only — the webhook itself determines the channel | `sefaria-down` |
| `STATUS_PAGE_URL` | Public URL used in Slack links and the sitemap | `https://status.sefaria.org` |
| `HEALTH_CHECK_INTERVAL` | Seconds between check cycles | `60` |
| `HEALTH_CHECK_RETRIES` | Retry attempts per request | `3` |
| `HEALTH_CHECK_RETRY_DELAY` | Seconds between retries | `10` |
| `ALERT_AFTER_CONSECUTIVE_FAILURES` | Default consecutive-failure threshold (per-service values override this) | `2` |
| `HEALTH_CHECK_RETENTION_DAYS` | Days of `HealthCheck` history to keep | `60` |
| `SEFARIA_HEALTH_URL` / `MCP_HEALTH_URL` / `AI_CHATBOT_HEALTH_URL` / `LINKER_HEALTH_URL` | Per-service URL overrides | see [base.py](config/settings/base.py) |

### Tuning a monitored service

Each entry in `MONITORED_SERVICES` accepts:

```python
{
    "name": "Linker",                       # display name + DB key
    "url": "https://www.sefaria.org/api/find-refs",
    "method": "POST",                       # default GET
    "expected_status": 202,                 # status that means "healthy"
    "timeout": 20,                          # per-request seconds
    "follow_redirects": False,              # default False
    "failure_threshold": 3,                 # consecutive cycles before DOWN
    "check_type": "async_two_phase",        # omit for a standard check
    "request_body": {"text": {"title": "", "body": "Job 1:1"}},
    "async_verification": {
        "base_url": "https://www.sefaria.org/api/async/",
        "max_poll_attempts": 10,
        "poll_interval": 1,                 # seconds between polls
    },
}
```

To **add a service**, append a dict here — no migration or code change is needed. The status page and alerter pick it up automatically.

## Management commands

```bash
# Run the scheduler loop: checks every interval + daily cleanup at 03:00 UTC
python manage.py run_checks

# Run a single check cycle and exit (handy for testing/CI)
python manage.py run_checks --once

# Delete HealthCheck rows older than the retention window
python manage.py cleanup_old_checks
python manage.py cleanup_old_checks --days 14   # override retention
python manage.py cleanup_old_checks --dry-run   # preview only
```

Cleanup runs automatically inside the scheduler; the standalone command exists for manual/maintenance use.

## The status page

[`monitoring/templates/monitoring/status.html`](monitoring/templates/monitoring/status.html) renders a self-refreshing public page:

- **Design** — A light, scholarly theme matching Sefaria's real brand: warm off-white background (`#FBFBFA`), serif headings, navy (`#18345D`) and gold (`#CCB479`) accents, the signature category-color top line, and gently rounded cards. Fonts are Roboto (UI) + Crimson Text (Sefaria's own Adobe Garamond fallback), loaded with system fallbacks so the page never blocks on the network. Tokens live at the top of [`style.css`](monitoring/static/monitoring/style.css); swap the `--font-serif` stack to Adobe Garamond via Typekit for an exact brand match.
- **Overall banner** — `All Systems Operational` / `Degraded Performance` / `Partial Outage` / `Major Outage`, computed from confirmed service states *and* active incident severity. *Major* = a high-severity incident or every service down; *partial* = some (not all) services down; *degraded* = nothing down but something slow or a medium-severity incident.
- **Per-service list** — Operational / Degraded / Down / Unknown, with last response time. "Down" requires the last `failure_threshold` checks to all fail (mirrors the alert logic so the page and Slack never disagree). "Degraded" means up but the latest response time exceeds `DEGRADED_RESPONSE_MS` (per-service override `degraded_threshold_ms`) — a page-only signal that never pages Slack. A down service's tooltip shows a *sanitized* hint (e.g. "Service unreachable", "Unexpected response (HTTP 521)") — never the raw internal error, which stays in the admin.
- **90-day uptime timeline** — Per-service daily bars (up / partial / down / no-data) with a visible color **legend**, a per-service uptime %, and an **aggregate "% overall"**, computed from `Outage` records (true downtime, not pruned like raw checks). Days before a service was first monitored show as "no data" rather than fake green. See `get_uptime_history` in [`views.py`](monitoring/views.py).
- **Response-time sparkline** — A small inline-SVG latency trend per service (last 24h, newest 40 samples) with a min/max/latest tooltip. Latency spikes read as upward peaks. Geometry is computed server-side in `get_response_time_sparklines` ([`views.py`](monitoring/views.py)) — no charting library or client-side work.
- **Status-aware Tanakh verse** — A Hebrew + English verse, with a deep link to Sefaria, chosen from a pool that matches the current status (reassuring when up, hopeful during an outage). Defined in [`views.py`](monitoring/views.py).
- **Incidents** — Operator-posted `Message` records (active + recent history), authored in the Django admin.
- **Scheduled maintenance** — Operator-posted `Maintenance` windows (title, description, affected services, start/end). While a window is in progress, affected services show "Under Maintenance" and their Slack alerts are suppressed (planned work shouldn't page anyone); the scheduler still records state. A blank "affected services" covers everything.
- **Dynamic favicon** — A colored status dot (SVG) reflects the overall status.
- **Live updates** — The page polls a cached `/api/status/` JSON endpoint every 30s and patches the banner and per-service rows in place; a slow 5-minute full reload picks up incidents, the quote, and uptime history. The page view and API are both cached for a short TTL (`@cache_page`).
- **Incident feeds** — RSS (`/history.rss`) and Atom (`/history.atom`) feeds of the incident history, built on Django's syndication framework, advertised for autodiscovery, and surfaced via a visible "Subscribe to updates" affordance. See [`feeds.py`](monitoring/feeds.py). Resolved incidents show their duration; all times are labelled UTC. When there are no incidents, the page shows an explicit "No incidents reported recently" reassurance.
- **Accessibility** — Status is conveyed by text labels as well as color; the overall banner is an `aria-live` region so screen readers hear status changes; the uptime bars are reachable as a labelled image (not hover-only); `prefers-reduced-motion` disables the pulsing dots; contrast meets WCAG AA.
- **Resilient by design** — The page survives the monitor's *own* database being down: all DB access is guarded, and a static, DB-free degraded page (and `unknown` JSON) is served instead of a 500. Self-contained `404`/`500` pages too.
- **SEO & social preview** — Open Graph + Twitter cards (with an **absolute** 1200×630 preview image so crawlers actually render it), `theme-color`, JSON-LD, `robots.txt`, and `sitemap.xml`, targeting the query "is Sefaria down". Regenerate the preview image with `python scripts/generate_og_image.py` (needs `pip install Pillow`; dev-only, not a runtime dependency).

To post an incident: log into `/admin/`, add a `Message` (severity high/medium), and it appears immediately. Mark it resolved via the bulk admin action. To schedule maintenance, add a `Maintenance Window` (title, affected services, start/end) — affected services then show "Under Maintenance" and their Slack alerts are suppressed for the duration.

## Admin access & security

The operator console is the standard Django admin at **`/admin/`** — i.e. `https://status.sefaria.org/admin/` in production, or `http://localhost:8000/admin/` locally. Log in with a Django **superuser**, created with:

```bash
python manage.py createsuperuser          # local
# in production, run inside the web container, e.g.:
docker compose exec web python manage.py createsuperuser
```

From there operators post incident `Message`s, schedule `Maintenance` windows, force-resolve stuck `Outage`s, and read the raw `HealthCheck` history. Each screen carries inline help describing what it does. The admin **landing page shows a live dashboard** — overall status, each service's current state and response time, and counts of open outages / active incidents / in-progress maintenance — so you see the system the moment you log in. Maintenance scope is chosen from **checkboxes of the configured services** (no free-typed names to mistype), and a window with an end before its start is rejected.

**Least-privilege staff.** A predefined **`Operators`** group (created by migration) grants exactly the rights to manage incidents and maintenance, force-resolve outages, and read health checks — but **not** to delete records or manage users. Add staff to this group (in the admin: Users → select user → add to *Operators*) and mark them `is_staff` rather than making them superusers.

**Brute-force protection.** Admin logins are protected by [`django-axes`](https://django-axes.readthedocs.io/): after `AXES_FAILURE_LIMIT` failed attempts (default 5) a username is locked for `AXES_COOLOFF_HOURS` (default 1h, returning HTTP 429); a successful login clears the count. Lockout is keyed on **username** (not IP, which is unreliable behind the proxy). Clear a lockout manually with `python manage.py axes_reset` (or `axes_reset_username <name>`).

Axes records what it sees under the **AXES** section of the admin:

- **Access attempts** — the live lockout state: one row per username with `failures_since_start`, IP, and user agent. Reset on a successful login or by `axes_reset`. This is what drives lockout.
- **Access failures** — a durable audit log of every failed attempt (enabled via `AXES_ENABLE_ACCESS_FAILURE_LOG`), kept even after attempts reset.
- **Access logs** — login/logout events with timestamps.

**Is it safe to leave `/admin/` publicly reachable?** Yes, with the defaults this project ships — but understand the trade-off:

- It is gated by Django authentication (login required) **plus `django-axes` login lockout**, and production enables HTTPS, secure + `HttpOnly` session/CSRF cookies, HSTS, `X-Frame-Options: DENY`, and content-type/XSS protections (see [`config/settings/production.py`](config/settings/production.py)).
- The blast radius is small: the admin only edits incident/maintenance content and reads monitoring data. It **cannot** change what is monitored or any thresholds — those live in [`config/settings/base.py`](config/settings/base.py) and ship with the image.
- The main risk of a public, default-path admin is automated credential-stuffing/bot noise against the login page. Mitigate by: using a **strong, unique superuser password** and few accounts; optionally restricting `/admin/` by IP or basic-auth at the Coolify/Traefik proxy, moving it off the default path, or adding 2FA (`django-otp`) / login rate-limiting if you want defense in depth.

If you'd rather not expose it at all, put `/admin/` behind the proxy's IP allowlist or a VPN — the public status page and its JSON/RSS endpoints don't depend on the admin.

## Deployment

Production runs in Docker, orchestrated by **[Coolify](https://coolify.io/)** (which provides the Traefik reverse proxy and TLS). The [`Dockerfile`](Dockerfile) is a multi-stage build that runs as a non-root user and serves static files via [WhiteNoise](https://whitenoise.readthedocs.io/) + gunicorn.

[`docker-compose.yml`](docker-compose.yml) defines four services:

| Service | Role | Command |
|---|---|---|
| `db` | PostgreSQL 16 | — |
| `web` | Status page + admin | [`scripts/web-entrypoint.sh`](scripts/web-entrypoint.sh) |
| `scheduler` | The health-check loop | `python manage.py run_checks` |
| `cleanup` | One-shot retention cleanup (profile `maintenance`) | `python manage.py cleanup_old_checks` |

On every deploy the **web** container runs the release entrypoint — **wait for DB** → `migrate` → `collectstatic` → `check --deploy` (informational) → `gunicorn` — so the schema and static assets are always current. The web container is the single migrator. Compose `depends_on` only *orders* containers (`service_started`), it does **not** gate `up` on healthchecks — gating made `docker compose up` block and could fail the whole deploy, so the entrypoint waits for the DB itself instead. The container healthcheck hits a lightweight `/healthz` (no DB, no page render), and production always allows `localhost` so that probe passes regardless of `ALLOWED_HOSTS`.

```bash
cp .env.example .env          # set SECRET_KEY, DB_PASSWORD, SLACK_WEBHOOK_URL, ALLOWED_HOSTS
docker compose up -d --build
docker compose logs -f scheduler
```

`docker-compose.override.yml` (git-ignored) adds local port mapping and a source mount; in production Coolify routes traffic to the `web` service, so no published ports are needed.

## Testing

149 tests cover the checker, state machine, alerter, scheduler, models, admin (incl. the dashboard, Operators group, and maintenance validation), cleanup, views, uptime history, response-time sparklines, degraded states, maintenance windows, incident feeds, and SEO.

```bash
# All tests (uses config.settings.test via pytest.ini)
.venv/Scripts/python -m pytest tests/ -v

# With coverage
.venv/Scripts/python -m pytest tests/ --cov=monitoring --cov-report=html
```

Tests mock all outbound HTTP and Slack calls, so they make no network requests. [`time-machine`](https://github.com/adamchainz/time-machine) is used to test time-dependent outage-duration logic.

## Project structure

```
config/
  settings/          base · development · production · test
  urls.py            admin/ + monitoring routes
monitoring/
  models.py          HealthCheck · Outage · Message · Maintenance
  views.py           status page, JSON API, healthz, uptime history,
                     sparklines, degraded logic, quotes, robots/sitemap
  feeds.py           RSS + Atom incident feeds
  admin.py           HealthCheck + Outage (read-only) · Message · Maintenance (CRUD)
  services/
    checker.py       HTTP checks, retries, parallelism, async two-phase
    state.py         StateTracker — transitions, Outage lifecycle
    alerter.py       Slack Block Kit alerts
    scheduler.py     APScheduler jobs (checks + cleanup, maintenance suppression)
  management/commands/
    run_checks.py        scheduler entrypoint
    cleanup_old_checks.py
  templatetags/
    monitoring_admin.py  admin landing-dashboard inclusion tag
  templates/ static/ migrations/   (incl. admin dashboard templates, Operators group migration)
scripts/
  web-entrypoint.sh  release flow for the web container (migrate, collectstatic, gunicorn)
tests/               149 tests + factories + fixtures
Dockerfile  docker-compose.yml  requirements.txt  .env.example  .gitattributes
```

## Operations & runbook

- **A service is red but I think it's fine** — Check the scheduler logs (`docker compose logs scheduler`). The page only goes red after `failure_threshold` consecutive failures; confirm the health endpoint really returns the expected status.
- **No Slack alerts** — Verify `SLACK_WEBHOOK_URL` is set in the `scheduler` service's environment; an empty value logs "skipping alert" and sends nothing.
- **Post an incident banner** — `/admin/` → Incident Messages → add (severity `high` contributes "Major Outage", `medium` contributes "Degraded Performance").
- **Schedule maintenance** — `/admin/` → Maintenance Windows → add (set affected services and a start/end). During the window those services show "Under Maintenance" and their Slack alerts are suppressed; uncheck *active* to cancel.
- **Tune the "degraded" threshold** — A service shows "Degraded" when its latest response time exceeds `DEGRADED_RESPONSE_MS` (global) or its per-service `degraded_threshold_ms`. This is page-only and never pages Slack.
- **A `recovered` outage is stuck open** (e.g. a recovery alert was missed) — `/admin/` → Outages → select it → **"Force-resolve selected outages"**. The scheduler reconciles with the database on its next cycle (≤ `HEALTH_CHECK_INTERVAL`): it clears its in-memory down state, and if the service is in fact still failing it opens a fresh outage and re-alerts. You never need to restart the scheduler to fix a dangling outage.
- **Database growing** — `HealthCheck` rows are pruned daily; adjust `HEALTH_CHECK_RETENTION_DAYS` or run `cleanup_old_checks` manually.
- **Tune noise** — Raise a service's `failure_threshold` in `base.py` if it flaps; lower it for faster paging.

## License

MIT
