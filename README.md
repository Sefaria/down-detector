# Sefaria Status Monitor

Real-time uptime monitoring system for Sefaria's critical services.

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://python.org)
[![Django 5.2](https://img.shields.io/badge/django-5.2-green.svg)](https://djangoproject.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Features

- **Health Checking**: Parallel HTTP checks with configurable retries (sefaria.org, MCP Server, AI Chatbot, Linker)
- **Async E2E Verification**: Two-phase check for the Linker API — verifies task submission *and* successful processing, with retries
- **Consecutive Failure Threshold**: Per-service configurable threshold — requires N consecutive failed check cycles before alerting, filtering brief blips
- **State Tracking**: Detects UP/DOWN transitions to prevent alert storms
- **Slack Alerts**: Block Kit notifications on confirmed outages, with accurate outage start time and downtime duration on recovery
- **Status Page**: Public dashboard at `status.sefaria.org` with 60s auto-refresh
- **Scheduled Cleanup**: Automatic daily purging of old records at 3 AM UTC

## Quick Start

### Local Development

```bash
# Clone and setup
git clone <repository-url>
cd sefaria-status
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Setup database
python manage.py migrate
python manage.py createsuperuser

# Run development server
python manage.py runserver

# Run health check scheduler
python manage.py run_checks
```

### Docker Deployment

```bash
# Copy and configure environment
cp .env.example .env
# Edit .env with your settings

# Build and start
docker compose up -d

# View logs
docker compose logs -f scheduler
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SECRET_KEY` | Django secret key | Required |
| `DEBUG` | Enable debug mode | `False` |
| `ALLOWED_HOSTS` | Comma-separated hosts | `status.sefaria.org` |
| `DATABASE_URL` | PostgreSQL connection URL | SQLite (dev) |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook | - |
| `SLACK_CHANNEL` | Alert channel name | `sefaria-down` |
| `STATUS_PAGE_URL` | Public status page URL | - |
| `HEALTH_CHECK_INTERVAL` | Check frequency (seconds) | `60` |
| `HEALTH_CHECK_RETRIES` | Retry attempts per check | `3` |
| `HEALTH_CHECK_RETRY_DELAY` | Delay between retries (seconds) | `10` |
| `ALERT_AFTER_CONSECUTIVE_FAILURES` | Default consecutive failures before alerting | `2` |
| `HEALTH_CHECK_RETENTION_DAYS` | Days to keep records | `60` |

### Monitored Services

Configured in `config/settings/base.py`:

```python
MONITORED_SERVICES = [
    {
        "name": "sefaria.org",
        "url": "https://www.sefaria.org/healthz",
        "method": "GET",
        "follow_redirects": True,
        "expected_status": 200,
        "failure_threshold": 2,  # alert after 2 consecutive failed cycles
    },
    {
        "name": "MCP Server",
        "url": "https://mcp.sefaria.org/healthz",
        "expected_status": 200,
        "failure_threshold": 2,
    },
    {
        "name": "AI Chatbot",
        "url": "https://chat-dev.sefaria.org/api/health",
        "expected_status": 200,
        "failure_threshold": 2,
    },
    {
        "name": "Linker",
        "url": "https://www.sefaria.org/api/find-refs",
        "method": "POST",
        "expected_status": 202,
        "check_type": "async_two_phase",
        "failure_threshold": 3,  # noisiest service, higher threshold
        "request_body": {"text": {"title": "", "body": "Job 1:1"}},
        "async_verification": {
            "base_url": "https://www.sefaria.org/api/async/",
            "max_poll_attempts": 10,
            "poll_interval": 1,
        },
    },
]
```

Each service's `failure_threshold` sets how many consecutive failed check cycles are required before a DOWN alert is sent to Slack. This filters brief blips that self-resolve. Recovery alerts always fire immediately on the first successful check. Falls back to `ALERT_AFTER_CONSECUTIVE_FAILURES` (default 2) if not set per service.

## Management Commands

```bash
# Run health check scheduler (includes auto-cleanup at 3 AM UTC)
python manage.py run_checks

# Run once (for testing)
python manage.py run_checks --once

# Manual cleanup (runs automatically via scheduler)
python manage.py cleanup_old_checks

# Dry run cleanup
python manage.py cleanup_old_checks --dry-run
```

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=monitoring --cov-report=html
```

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   APScheduler   │────▶│  Health Checker │────▶│   PostgreSQL    │
│   (run_checks)  │     │    (httpx)      │     │   (HealthCheck) │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                               │
                               ▼
                        ┌─────────────────┐
                        │  State Tracker  │
                        │ (UP/DOWN detect)│
                        └─────────────────┘
                               │
                               ▼
                        ┌─────────────────┐
                        │  Slack Alerter  │
                        │  (Block Kit)    │
                        └─────────────────┘
```

## License

MIT