# Sefaria Uptime Monitoring System — Comprehensive Project Plan

**Date:** February 8, 2026  
**Version:** 1.0  
**Project Codename:** `sefaria-status`

---

## 1. Executive Summary

This document is a deep research-and-planning guide for building a standalone uptime monitoring system for Sefaria's critical services. The system runs independently of the main Sefaria Kubernetes cluster, hosted on a GCP VM via Coolify, with PostgreSQL for persistence, Slack for alerting, Django Admin for incident management, and a clean public-facing status page.

The plan covers architecture decisions, technology choices (with rationale), service endpoint discovery, TDD strategy, phased implementation order, data modeling, deployment topology, and risk mitigation — all grounded in 2025/2026 best practices.

---

## 2. Services Under Monitoring — Endpoint Research

### 2.1 sefaria.org (Production Website)

- **Primary URL:** `https://www.sefaria.org`
- **Health check strategy:** The Sefaria-Project codebase is a Django/MongoDB app on Kubernetes. There is no publicly documented `/healthz` endpoint in the API docs. The stories reference checking `/healthz` expecting `HTTP 200 + "already": true`, which suggests an internal Kubernetes readiness probe format.
- **Recommended approach:** Perform a lightweight GET to `https://www.sefaria.org/api/texts/Genesis.1.1` (a known stable text endpoint). A `200` with valid JSON confirms the full application stack (Django, MongoDB, Elasticsearch) is operational. Alternatively, a simple `HEAD` or `GET` to `https://www.sefaria.org` checking for `200` status. Define the health check URL as configurable in Django settings so it can be updated if/when a `/healthz` is exposed publicly.
- **Timeout recommendation:** 10 seconds (Sefaria pages can be heavy; the API is lighter).

### 2.2 Linker Server

- **Primary URL:** `https://www.sefaria.org/api/find-refs` (the Linker v3 API endpoint)
- **Health check strategy:** The Linker API is a POST endpoint that returns `202` with a task ID for async processing. A health check doesn't need to submit a full job — instead, check reachability. Options:
  - Hit `https://www.sefaria.org/api/find-refs` with a minimal POST body and confirm a `202` response.
  - Alternatively, if the linker is deployed as a separate service with its own hostname (likely behind the same ingress), the health check URL should be configurable.
- **Note:** The Linker JavaScript (`linker.v3.js`) is served from `www.sefaria.org`, suggesting the linker shares the main infrastructure. Confirm with the team whether there's a dedicated linker service hostname.

### 2.3 MCP Server

- **Primary URL:** `https://developers.sefaria.org/mcp` (the official Sefaria MCP SSE endpoint)
- **Health check strategy:** The MCP server is a FastMCP (Python) server. In production, it exposes an SSE endpoint at `/sse`. A health check should:
  - Perform an HTTP GET to the MCP server's base URL or `/sse` endpoint.
  - Expect a `200` response (SSE connections return `200` with `text/event-stream` content type).
  - Alternatively, if there's a `/health` endpoint on the FastMCP server, use that.
- **Timeout recommendation:** 5 seconds. MCP servers are lightweight.

### 2.4 Export Server

- **Primary URL:** This likely refers to the data export functionality. Sefaria's export data lives at `https://github.com/Sefaria/Sefaria-Export`, but if there's a running export server (e.g., for generating MongoDB dumps or text exports on demand), the URL needs to be confirmed with the team.
- **Health check strategy:** Configurable URL. If it's a web service, a simple GET to its health endpoint. If it's a GitHub-based static export, perhaps check that `https://raw.githubusercontent.com/Sefaria/Sefaria-Export/master/...` returns `200`.
- **Action item:** Confirm with Sefaria engineering what "export server" refers to and its production URL.

### 2.5 Service Configuration Design

All services should be defined as a list of dictionaries in Django settings, making it trivial to add new services later:

```python
# settings.py (conceptual — no implementation yet)
MONITORED_SERVICES = [
    {
        "name": "sefaria.org",
        "url": "https://www.sefaria.org/api/texts/Genesis.1.1",
        "method": "GET",
        "expected_status": 200,
        "timeout": 10,
        "verify_body": {"key": "he", "exists": True},  # optional body validation
    },
    {
        "name": "Linker",
        "url": "https://www.sefaria.org/api/find-refs",
        "method": "POST",
        "expected_status": 202,
        "timeout": 10,
        "request_body": {"text": {"title": "health check"}},
    },
    {
        "name": "MCP Server",
        "url": "https://developers.sefaria.org/mcp",
        "method": "GET",
        "expected_status": 200,
        "timeout": 5,
    },
    {
        "name": "Export Server",
        "url": "TBD — confirm with team",
        "method": "GET",
        "expected_status": 200,
        "timeout": 10,
    },
]
```

---

## 3. Architecture & Technology Decisions

### 3.1 Django Version

- **Recommendation:** Django 5.2 LTS (released April 2025, supported through April 2028).
- **Rationale:** It's the current Long-Term Support release. Python 3.12 or 3.13 support. Stable, battle-tested, and will receive security patches for the life of this project.

### 3.2 Python Version

- **Recommendation:** Python 3.12 or 3.13.
- **Rationale:** 3.12 is very stable by now; 3.13 adds free-threading (experimental) but 3.12 is the safer bet for production. Django 5.2 supports both.

### 3.3 Background Task Scheduler — APScheduler

The stories specify APScheduler. After research, this is the right choice for this project:

| Option | Verdict | Rationale |
|--------|---------|-----------|
| **APScheduler** | ✅ Selected | Perfect for periodic scheduling. No broker required. Runs in-process. Minimal setup. Handles interval and cron triggers. |
| Celery | ❌ Overkill | Requires Redis/RabbitMQ. Too heavy for simple periodic pings. |
| Django-Q2 | ❌ Close second | Good option but adds ORM-based task queue overhead we don't need. Cron-based scheduling limited to 1-minute resolution. |
| Huey | ❌ Not needed | Good lightweight queue, but we don't need a task queue — we need a scheduler. |
| django-tasks (DEP 0014) | ❌ Not ready | The reference implementation doesn't support scheduled/periodic tasks yet. Only one-off tasks. |

**APScheduler specifics:**
- Use `BackgroundScheduler` (thread-based, works inside Django's WSGI process)
- Use `IntervalTrigger` with `seconds=60` for the main check loop
- Use `django-apscheduler` package for persistence of job metadata in the DB (optional but helpful for debugging)
- Start the scheduler in a Django `AppConfig.ready()` method, guarded by `RUN_MAIN` check for dev server or managed via a separate management command for production

**Critical concern — duplicate schedulers:** When running with Gunicorn (multiple workers), APScheduler will start in each worker process. Solutions:
1. Run the scheduler in a **separate management command** (`python manage.py run_health_checks`) as its own process/container. This is the recommended approach.
2. Use a file lock or DB lock to ensure only one scheduler runs.
3. Use Gunicorn's `--preload` with a startup check.

**Recommendation:** Run the health check scheduler as a separate container/process in the Docker Compose stack. This cleanly separates concerns and avoids the multi-worker problem entirely.

### 3.4 HTTP Client — httpx

- **Recommendation:** `httpx` (sync mode for simplicity, or async if we want concurrent checks).
- **Rationale:** Modern, actively maintained, supports both sync and async. Better timeout handling than `requests`. Type hints. Built-in connection pooling.
- **Alternative considered:** `requests` — still fine, but httpx is the modern standard in 2025/2026 and handles edge cases (timeouts, HTTP/2) better.

### 3.5 Retry Logic — tenacity

- **Recommendation:** Use the `tenacity` library for retry decorators.
- **Rationale:** Battle-tested, works with both sync and async. Supports exponential backoff, jitter, custom stop/retry conditions. Cleaner than hand-rolling retry loops.
- **Configuration:** 3 retries, 10-second spacing, only retry on connection errors and 5xx status codes.

### 3.6 Slack Integration — slack-sdk

- **Recommendation:** Use `slack-sdk` (`WebhookClient` class) for sending alerts.
- **Rationale:** Official Slack SDK. Supports Block Kit for rich formatting. Handles retries natively. Type-safe.
- **Alternative considered:** Raw `httpx.post()` to the webhook URL — simpler but loses Block Kit builder ergonomics and built-in retry handling.

### 3.7 Database — PostgreSQL via Coolify

- **Recommendation:** PostgreSQL 16+ managed as a Coolify service.
- **Rationale:** Coolify has first-class PostgreSQL support with one-click provisioning, automated backups, and internal networking between containers. Django 5.2 supports PostgreSQL 14+.
- **Connection:** Use the internal Coolify network URL (e.g., `postgres://user:pass@pg-service:5432/sefaria_status`). Stored as `DATABASE_URL` environment variable, parsed with `dj-database-url`.

### 3.8 WSGI Server — Gunicorn

- **Recommendation:** Gunicorn with 2-4 workers.
- **Rationale:** Standard, lightweight, proven. The status page will have very low traffic.

### 3.9 Static Files — WhiteNoise

- **Recommendation:** WhiteNoise middleware for serving static CSS/JS.
- **Rationale:** No need for Nginx/Caddy for a low-traffic status page. WhiteNoise is the simplest production-ready solution for Django static files.

### 3.10 Environment Configuration — django-environ or environs

- **Recommendation:** `django-environ` for reading settings from environment variables.
- **Rationale:** Clean `.env` file support, `DATABASE_URL` parsing, type casting.

---

## 4. Data Models — Detailed Design

### 4.1 HealthCheck Model

```
HealthCheck
├── id: BigAutoField (PK)
├── service_name: CharField(max_length=100, db_index=True)
├── status: CharField(max_length=10, choices=["up", "down"])
├── response_time_ms: PositiveIntegerField(null=True)
├── status_code: PositiveSmallIntegerField(null=True)
├── error_message: TextField(blank=True, default="")
├── checked_at: DateTimeField(db_index=True)
├── created_at: DateTimeField(auto_now_add=True)
│
├── Meta:
│   ├── indexes: [
│   │   Index(fields=["service_name", "-checked_at"]),  # Fast latest-per-service lookup
│   │   Index(fields=["-checked_at"]),                   # Fast timeline queries
│   │]
│   ├── ordering: ["-checked_at"]
│   └── get_latest_by: "checked_at"
```

**Design decisions:**
- `status` as CharField with choices (not BooleanField) — more expressive, allows future states like "degraded" or "timeout".
- `response_time_ms` — useful for the status page sparkline/timeline, and for detecting degradation before outage.
- `status_code` and `error_message` — diagnostic data for Slack alerts and debugging.
- Composite index on `(service_name, -checked_at)` — this is the critical query path for "latest status per service."

**Retention policy:** A management command (`cleanup_health_checks`) that deletes records older than 60 days, run daily via APScheduler or cron.

### 4.2 Message (Incident) Model

```
Message
├── id: BigAutoField (PK)
├── severity: CharField(max_length=20, choices=["high", "medium", "resolved"])
├── text: TextField()
├── active: BooleanField(default=True, db_index=True)
├── created_at: DateTimeField(auto_now_add=True)
├── updated_at: DateTimeField(auto_now=True)
│
├── Meta:
│   ├── ordering: ["-created_at"]
│   └── verbose_name_plural: "Messages"
│
├── __str__: f"[{severity.upper()}] {text[:60]}{'...' if len(text) > 60 else ''}"
```

**Design decisions:**
- `severity` as CharField with choices — easily extensible.
- `active` with `db_index` — frequently filtered in queries.
- `updated_at` with `auto_now` — tracks when incidents are resolved (toggling `active` to False).
- No artificial constraint on number of active incidents — multiple simultaneous incidents are realistic.

---

## 5. State Machine — Alert Logic

The Slack alerting system must track **state transitions**, not absolute state. This prevents alert storms.

```
                 ┌──────────┐
     Service     │          │    All retries pass
     check OK ──►│    UP    │◄───────────────────┐
                 │          │                     │
                 └────┬─────┘                     │
                      │                           │
                      │ Single check fails        │
                      ▼                           │
                 ┌──────────┐                     │
                 │ CHECKING │ (retry 1..3)        │
                 │          │                     │
                 └────┬─────┘                     │
                      │                           │
                      │ All retries fail          │
                      ▼                           │
                 ┌──────────┐                     │
                 │   DOWN   ├─────────────────────┘
                 │          │    Next check passes
                 └──────────┘

Slack alerts fire ONLY on:
  UP → DOWN  (send "🔴 Service X is DOWN" alert)
  DOWN → UP  (send "🟢 Service X is RECOVERED" alert)
```

**Implementation approach:**
- Maintain an in-memory dictionary `_service_states: dict[str, str]` that tracks the last-known state of each service.
- On startup, initialize from the latest HealthCheck record per service from the database.
- After each check cycle (including retries), compare the new state to the stored state.
- Only fire Slack alerts on state transitions.
- Persist the state via the HealthCheck model (the latest row IS the current state).

---

## 6. Slack Message Design

### 6.1 Down Alert

```
🔴  Service Down: sefaria.org

Status:      DOWN
Since:       2026-02-08 14:23:00 UTC
HTTP Code:   503
Error:       Service Unavailable
Retries:     3/3 failed

Status Page: https://status.sefaria.org
```

Using Block Kit:
- Header block with service name and 🔴 emoji
- Section block with mrkdwn fields for status, timestamp, HTTP code, error
- Context block with link to status page

### 6.2 Recovery Alert

```
🟢  Service Recovered: sefaria.org

Status:      UP
Recovered:   2026-02-08 14:27:00 UTC
Downtime:    ~4 minutes
Response:    234ms

Status Page: https://status.sefaria.org
```

### 6.3 Implementation Notes

- Use `slack_sdk.webhook.WebhookClient` — NOT raw HTTP posts.
- Webhook URL stored in `SLACK_WEBHOOK_URL` environment variable.
- Always include a `text` fallback alongside `blocks` (required by Slack for notifications).
- No repeated alerts — state transition logic handles this.

---

## 7. Status Page Design

### 7.1 Layout (inspired by Shopify/Sentry/Cloudflare status pages)

```
┌──────────────────────────────────────────────────┐
│  🟢  All Systems Operational                     │
│  (or 🟡 Partial Outage / 🔴 Major Outage)       │
├──────────────────────────────────────────────────┤
│                                                  │
│  SERVICE STATUS                                  │
│  ─────────────────────────────────────────────── │
│  🟢  sefaria.org          Operational    234ms   │
│  🟢  Linker Server        Operational    89ms    │
│  🔴  MCP Server           Down           —       │
│  🟢  Export Server         Operational    156ms   │
│                                                  │
├──────────────────────────────────────────────────┤
│                                                  │
│  ⚠️  ACTIVE INCIDENTS                            │
│  ─────────────────────────────────────────────── │
│  🔴 HIGH — MCP server experiencing connectivity  │
│  issues. Investigating. (Feb 8, 2:23 PM)         │
│                                                  │
├──────────────────────────────────────────────────┤
│                                                  │
│  📋  INCIDENT HISTORY                            │
│  ─────────────────────────────────────────────── │
│  ✅ RESOLVED — Scheduled maintenance on export   │
│  server completed. (Feb 7, 10:00 AM)             │
│  ✅ RESOLVED — Brief sefaria.org latency spike.  │
│  (Feb 5, 3:15 PM)                                │
│                                                  │
├──────────────────────────────────────────────────┤
│  Powered by Sefaria  •  Updated 30s ago          │
└──────────────────────────────────────────────────┘
```

### 7.2 Technical Implementation

- **Template engine:** Django templates. No JavaScript framework needed.
- **Styling:** A single CSS file. Minimal, clean design. CSS variables for the color system:
  - `--color-up: #22c55e` (green)
  - `--color-down: #ef4444` (red)
  - `--color-degraded: #f59e0b` (amber)
  - `--color-resolved: #6b7280` (gray)
- **Caching:** Use Django's `cache_page(30)` decorator on the status view for 30-second caching. This prevents DB hammering on public pages.
- **View query strategy:**
  1. Latest HealthCheck per service: `HealthCheck.objects.filter(service_name=name).order_by('-checked_at').first()` for each service, or a single raw SQL with `DISTINCT ON`.
  2. Active incidents: `Message.objects.filter(active=True).order_by('-created_at')`
  3. Historical incidents: `Message.objects.filter(active=False).order_by('-created_at')[:30]`
- **Overall status logic:**
  - All services UP + no active incidents → "All Systems Operational" (green)
  - Any service DOWN or any HIGH active incident → "Major Outage" (red)
  - Any MEDIUM active incident → "Partial Outage" (amber)

---

## 8. Django Admin Configuration

### 8.1 HealthCheckAdmin

- `list_display`: service_name, status, response_time_ms, status_code, checked_at
- `list_filter`: service_name, status
- `search_fields`: service_name, error_message
- `readonly_fields`: all (health checks are system-generated, not manually edited)
- `date_hierarchy`: checked_at
- `ordering`: -checked_at

### 8.2 MessageAdmin

- `list_display`: severity, text_preview (first 80 chars), active, created_at
- `list_filter`: severity, active
- `list_editable`: active (toggle directly from list view)
- `search_fields`: text
- `ordering`: -created_at
- `actions`: "Mark as Resolved" bulk action (sets active=False, severity="resolved")

---

## 9. Project Structure

```
sefaria-status/
├── manage.py
├── pyproject.toml              # Modern Python project config (replaces setup.py + requirements.txt)
├── Dockerfile
├── docker-compose.yml          # For Coolify deployment
├── .env.example
├── pytest.ini                  # Or pyproject.toml [tool.pytest.ini_options]
│
├── config/                     # Project-level config (not an app)
│   ├── __init__.py
│   ├── settings/
│   │   ├── __init__.py
│   │   ├── base.py             # Shared settings
│   │   ├── development.py      # Local dev overrides
│   │   ├── production.py       # Production overrides
│   │   └── test.py             # Test overrides (in-memory SQLite option, faster)
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
│
├── monitoring/                 # Main Django app
│   ├── __init__.py
│   ├── apps.py
│   ├── models.py               # HealthCheck + Message models
│   ├── admin.py                # Admin configuration
│   ├── views.py                # Status page view
│   ├── urls.py
│   ├── services/
│   │   ├── __init__.py
│   │   ├── checker.py          # Health check logic (httpx + tenacity)
│   │   ├── alerter.py          # Slack alerting logic
│   │   ├── scheduler.py        # APScheduler setup and job registration
│   │   └── state.py            # State transition tracking
│   ├── management/
│   │   └── commands/
│   │       ├── run_checks.py   # Management command to start the scheduler
│   │       └── cleanup.py      # Retention policy cleanup
│   ├── templates/
│   │   └── monitoring/
│   │       └── status.html     # Status page template
│   ├── static/
│   │   └── monitoring/
│   │       └── style.css       # Status page styles
│   └── migrations/
│
├── tests/                      # All tests here (not inside the app)
│   ├── __init__.py
│   ├── conftest.py             # Shared fixtures, factories
│   ├── factories.py            # factory_boy factories
│   ├── test_models.py          # Model unit tests
│   ├── test_checker.py         # Health check service tests
│   ├── test_alerter.py         # Slack alerter tests
│   ├── test_state.py           # State machine tests
│   ├── test_views.py           # Status page view tests
│   └── test_admin.py           # Admin configuration tests
```

---

## 10. TDD Strategy

### 10.1 Testing Stack

| Tool | Purpose |
|------|---------|
| `pytest` + `pytest-django` | Test runner, Django integration |
| `factory_boy` | Model instance factories |
| `pytest-mock` / `unittest.mock` | Mocking httpx calls, Slack webhook |
| `freezegun` or `time-machine` | Time travel for timestamp-dependent tests |
| `coverage` | Code coverage reporting |
| `pytest-cov` | Coverage integration with pytest |

### 10.2 pytest Configuration

```ini
# pyproject.toml [tool.pytest.ini_options]
DJANGO_SETTINGS_MODULE = "config.settings.test"
testpaths = ["tests"]
python_files = "test_*.py"
python_classes = "Test*"
python_functions = "test_*"
addopts = "--reuse-db --tb=short --strict-markers -v"
markers = [
    "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    "integration: marks tests that hit external services",
]
```

### 10.3 TDD Order — Red/Green/Refactor per Story

**Story 1: HealthCheck Model (write tests FIRST for each)**

1. `test_healthcheck_creation` — Can create a HealthCheck with all fields
2. `test_healthcheck_ordering` — Default ordering is `-checked_at`
3. `test_healthcheck_str` — String representation is useful
4. `test_healthcheck_index_exists` — Composite index exists on service_name + checked_at

**Story 2: Health Checker Service**

5. `test_check_service_success` — Mock httpx, return 200, get "up" result
6. `test_check_service_failure` — Mock httpx, return 500, get "down" result
7. `test_check_service_timeout` — Mock httpx timeout, get "down" result
8. `test_check_service_connection_error` — Mock connection refused, get "down"
9. `test_retry_logic_eventual_success` — First 2 fail, third succeeds → "up"
10. `test_retry_logic_all_fail` — All 3 retries fail → "down"
11. `test_check_persists_to_db` — After check, a HealthCheck record exists
12. `test_check_measures_response_time` — response_time_ms is populated

**Story 3: State Tracking**

13. `test_state_initializes_from_db` — Loads last known state from HealthCheck table
14. `test_state_detects_up_to_down` — Returns transition type "went_down"
15. `test_state_detects_down_to_up` — Returns transition type "recovered"
16. `test_state_no_transition_when_stable` — Returns None when state hasn't changed

**Story 4: Slack Alerter**

17. `test_alert_sends_on_down_transition` — Mock WebhookClient, verify `.send()` called with down payload
18. `test_alert_sends_on_recovery` — Verify recovery message sent
19. `test_alert_not_sent_when_no_transition` — Verify `.send()` NOT called
20. `test_alert_includes_service_name` — Payload contains service name
21. `test_alert_includes_diagnostic_info` — Payload contains HTTP code, error
22. `test_alert_uses_block_kit` — Payload contains `blocks` key

**Story 5: Message Model + Admin**

23. `test_message_creation` — Can create a Message with all fields
24. `test_message_default_active` — New messages default to `active=True`
25. `test_message_str` — String representation shows severity and truncated text
26. `test_message_admin_registered` — MessageAdmin is registered
27. `test_active_messages_query` — Filter returns only active messages

**Story 6: Status Page View**

28. `test_status_page_returns_200` — GET /status/ returns 200
29. `test_status_page_shows_all_services` — All service names appear in response
30. `test_status_page_shows_active_incidents` — Active messages appear
31. `test_status_page_shows_resolved_incidents` — Resolved messages appear
32. `test_status_page_overall_status_all_up` — Shows "All Systems Operational"
33. `test_status_page_overall_status_partial` — Shows "Partial Outage" when medium incident
34. `test_status_page_overall_status_major` — Shows "Major Outage" when service down
35. `test_status_page_caching` — Response includes cache headers

---

## 11. Phased Implementation Plan

### Phase 1: Foundation (Stories 1 + 2)

**Goal:** Django project scaffold, models, migrations, health check service with retry logic.

**Steps:**
1. Initialize Django project with `config/` settings split
2. Write model tests → create `HealthCheck` model → run migrations
3. Write checker service tests → implement `checker.py` with httpx + tenacity
4. Write state tracking tests → implement `state.py`
5. Wire up APScheduler in `scheduler.py`
6. Create `run_checks` management command
7. Configure Django Admin for HealthCheck (read-only)

**Definition of Done:**
- `pytest` passes all Phase 1 tests
- `python manage.py run_checks` starts checking services on a 60s interval
- Health check results appear in DB and Django Admin
- Retry logic confirmed via tests (3 attempts, 10s spacing)

### Phase 2: Alerting (Story 5)

**Goal:** Slack alerts on state transitions.

**Steps:**
1. Write alerter tests → implement `alerter.py` with `slack_sdk.WebhookClient`
2. Write integration tests for state → alerter pipeline
3. Wire alerter into the scheduler's post-check callback
4. Test end-to-end with a real Slack webhook (manual integration test)

**Definition of Done:**
- Slack receives alerts ONLY on state transitions
- Down alerts include service name, timestamp, HTTP code, error
- Recovery alerts include downtime duration
- No duplicate alerts on repeated failures

### Phase 3: Incident Management (Story 3)

**Goal:** Message model with Django Admin for manual incident management.

**Steps:**
1. Write Message model tests → create model → run migrations
2. Configure `MessageAdmin` with filtering, bulk actions
3. Verify admin CRUD operations work

**Definition of Done:**
- Can create/edit/deactivate incident messages via Admin
- Messages filterable by severity and active status
- "Mark as Resolved" bulk action works

### Phase 4: Status Page (Story 4)

**Goal:** Public-facing status page.

**Steps:**
1. Write view tests → implement status view
2. Create HTML template with CSS
3. Add caching (30-second `cache_page`)
4. Test overall status logic (all up, partial, major outage)

**Definition of Done:**
- Status page loads in < 500ms
- Shows current status of all services
- Shows active and historical incidents
- Caches for 30 seconds
- Clean, professional design

### Phase 5: Deployment & Hardening

**Goal:** Production deployment via Coolify on GCP.

**Steps:**
1. Create `Dockerfile` (multi-stage: builder + runtime)
2. Create `docker-compose.yml` with web, scheduler, and postgres services
3. Configure Coolify project with PostgreSQL service
4. Set environment variables in Coolify
5. Configure domain (e.g., `status.sefaria.org`) via Cloudflare
6. Set up daily backup for PostgreSQL
7. Add retention cleanup to scheduler (delete records > 60 days)
8. Smoke test everything end-to-end

---

## 12. Docker & Deployment Topology

### 12.1 docker-compose.yml Structure

```yaml
# Conceptual — not final implementation
services:
  web:
    build: .
    command: gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2
    environment:
      - DATABASE_URL=postgres://...
      - SLACK_WEBHOOK_URL=...
      - DJANGO_SETTINGS_MODULE=config.settings.production
    ports:
      - "8000:8000"

  scheduler:
    build: .
    command: python manage.py run_checks
    environment:
      - DATABASE_URL=postgres://...
      - SLACK_WEBHOOK_URL=...
      - DJANGO_SETTINGS_MODULE=config.settings.production
    # No port exposed — this is a background worker only
```

PostgreSQL is provisioned separately as a Coolify-managed service (not in this compose file), connected via Coolify's internal Docker network.

### 12.2 Dockerfile Strategy

- **Base image:** `python:3.12-slim`
- **Multi-stage build:** Builder stage installs dependencies, runtime stage copies only what's needed
- **Non-root user:** Run as `app` user
- **Health check:** `HEALTHCHECK CMD curl -f http://localhost:8000/status/ || exit 1`
- **Static files:** Collected during build (`collectstatic`)

### 12.3 Coolify-Specific Notes

- Connect to the same Docker network as the PostgreSQL service
- Use Coolify's "Docker Compose" deployment method
- Set environment variables in Coolify's UI (not in the compose file for secrets)
- Enable auto-deploy on push to main branch
- Coolify handles SSL termination and domain routing

---

## 13. Dependency List

### Runtime

| Package | Version (minimum) | Purpose |
|---------|--------------------|---------|
| Django | 5.2 | Web framework |
| gunicorn | 23+ | WSGI server |
| psycopg[binary] | 3.2+ | PostgreSQL adapter (psycopg3, not psycopg2) |
| dj-database-url | 2.3+ | DATABASE_URL parsing |
| django-environ | 0.12+ | Environment variable management |
| whitenoise | 6.8+ | Static file serving |
| httpx | 0.28+ | HTTP client for health checks |
| tenacity | 9+ | Retry logic |
| APScheduler | 3.10+ | Background task scheduling |
| slack-sdk | 3.34+ | Slack webhook integration |

### Development / Testing

| Package | Purpose |
|---------|---------|
| pytest | Test runner |
| pytest-django | Django test integration |
| pytest-cov | Coverage reporting |
| pytest-mock | Mock integration |
| factory-boy | Model factories |
| time-machine | Time freezing for tests |
| ruff | Linting + formatting (replaces flake8 + black + isort) |
| pre-commit | Git hook management |

---

## 14. Security Considerations

1. **Slack webhook URL:** Stored ONLY in environment variables. Never committed to code.
2. **Django SECRET_KEY:** Generated per environment, stored in env vars.
3. **Database credentials:** Internal Coolify network only. Not publicly exposed.
4. **Admin access:** Django Admin behind `/admin/` with strong password. Consider adding `django-admin-honeypot` or IP restriction.
5. **ALLOWED_HOSTS:** Set explicitly to the status page domain.
6. **CSRF/Security middleware:** Enable all Django security middleware (`SecurityMiddleware`, `X-Content-Type-Options`, `X-Frame-Options`, HSTS).
7. **Rate limiting:** The status page is public and cacheable. Consider `django-ratelimit` if abuse is a concern.
8. **No sensitive data on status page:** Only service names and up/down status. No internal URLs or error details exposed publicly.

---

## 15. Monitoring the Monitor (Meta-Monitoring)

Since this is a monitoring system, we should consider what happens when IT goes down:

1. **Coolify notifications:** Coolify can send Telegram/email notifications when deployments fail or containers crash. Enable this.
2. **GCP VM monitoring:** Use GCP's built-in monitoring to alert if the VM itself becomes unreachable.
3. **External ping:** Consider a free external monitor (e.g., UptimeRobot free tier) pointed at `status.sefaria.org` to alert if the status page itself goes down.
4. **PostgreSQL backups:** Coolify supports automated PostgreSQL backups. Enable daily backups with 7-day retention.

---

## 16. Future Enhancements (Out of Scope for v1)

- Response time trending graphs (Chart.js or similar)
- RSS/Atom feed for incidents
- Email subscription for status updates
- API endpoint (`/api/status/`) for programmatic access
- Uptime percentage calculations (e.g., "99.95% uptime last 30 days")
- Integration with PagerDuty or Opsgenie for on-call escalation
- Multi-region checks (check from US + EU)
- Certificate expiry monitoring
- Custom check types (e.g., response body validation, database query checks)

---

## 17. Open Questions for the Team

1. **Export Server:** What is the production URL for the export server? Is it a standalone service or part of the main Sefaria app?
2. **Health endpoints:** Do any of the services (sefaria.org, linker, MCP, export) have dedicated `/healthz` or `/health` endpoints? The stories mention expecting `"already": true` in the response — which services return this?
3. **Slack channel:** Which Slack channel should alerts go to? Should down vs. recovery go to different channels?
4. **Domain:** Is `status.sefaria.org` the intended domain? Is there a Cloudflare account ready for DNS configuration?
5. **Access control:** Should the Django Admin be accessible to all team members, or restricted to specific roles?
6. **Linker server:** Is the linker a separate service with its own hostname, or does it share `www.sefaria.org`?

---

## 18. Summary — Implementation Checklist

| # | Task | Phase | Tests First? |
|---|------|-------|-------------|
| 1 | Django project scaffold + settings split | 1 | N/A |
| 2 | HealthCheck model + migrations | 1 | ✅ |
| 3 | Health checker service (httpx + tenacity) | 1 | ✅ |
| 4 | State transition tracker | 1 | ✅ |
| 5 | APScheduler setup + management command | 1 | ✅ |
| 6 | HealthCheck Admin (read-only) | 1 | ✅ |
| 7 | Slack alerter service | 2 | ✅ |
| 8 | Alerter wired into scheduler | 2 | ✅ |
| 9 | Message model + migrations | 3 | ✅ |
| 10 | Message Admin with bulk actions | 3 | ✅ |
| 11 | Status page view + template + CSS | 4 | ✅ |
| 12 | Cache layer for status page | 4 | ✅ |
| 13 | Dockerfile + docker-compose.yml | 5 | N/A |
| 14 | Coolify deployment + PostgreSQL | 5 | N/A |
| 15 | Domain + SSL via Cloudflare | 5 | N/A |
| 16 | Retention cleanup command | 5 | ✅ |
| 17 | End-to-end smoke test | 5 | N/A |