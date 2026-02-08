# Sefaria Status Monitor

Real-time uptime monitoring system for Sefaria's critical services.

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://python.org)
[![Django 5.2](https://img.shields.io/badge/django-5.2-green.svg)](https://djangoproject.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Features

- **Health Checking**: Periodic HTTP checks with configurable retries
- **State Tracking**: Detects UP/DOWN transitions to prevent alert storms
- **Slack Alerts**: Block Kit formatted notifications on state changes
- **Status Page**: Public dashboard at `status.sefaria.org`
- **Retention Cleanup**: Automatic old record purging

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
| `HEALTH_CHECK_RETRIES` | Retry attempts | `3` |
| `HEALTH_CHECK_RETENTION_DAYS` | Days to keep records | `30` |

### Monitored Services

Configured in `config/settings/base.py`:

```python
MONITORED_SERVICES = [
    {
        "name": "sefaria.org",
        "url": "https://www.sefaria.org/healthz",
        "method": "GET",
        "expected_status": 302,
    },
    {
        "name": "Linker",
        "url": "https://www.sefaria.org/api/find-refs",
        "method": "POST",
        "expected_status": 202,
        "request_body": {"text": "health check"},
    },
    # ...
]
```

## Management Commands

```bash
# Run health check scheduler
python manage.py run_checks

# Run once (for testing)
python manage.py run_checks --once

# Cleanup old records
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