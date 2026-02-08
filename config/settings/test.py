"""
Test settings for pytest.
"""
from .base import *  # noqa: F401, F403

DEBUG = False

# Use in-memory SQLite for fast tests
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Speed up password hashing in tests
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# Disable Slack in tests
SLACK_WEBHOOK_URL = ""

# Shorter intervals for tests
HEALTH_CHECK_INTERVAL = 1
HEALTH_CHECK_RETRIES = 2
HEALTH_CHECK_RETRY_DELAY = 1

# Test services (mocked)
MONITORED_SERVICES = [
    {
        "name": "test-service",
        "url": "https://test.example.com/healthz",
        "method": "GET",
        "expected_status": 200,
        "timeout": 5,
    },
]
