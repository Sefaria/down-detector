"""
Base Django settings for sefaria-status project.
"""
import os
from pathlib import Path

import environ

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Initialize environ
env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
)

# Read .env file if it exists
environ.Env.read_env(os.path.join(BASE_DIR, ".env"))

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env("SECRET_KEY", default="django-insecure-dev-key-change-in-production")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env("DEBUG")

ALLOWED_HOSTS = env("ALLOWED_HOSTS")

# Application definition
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "axes",  # admin login lockout / brute-force protection
    # Local apps
    "monitoring",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # AxesMiddleware must be last so it can render lockout responses.
    "axes.middleware.AxesMiddleware",
]

# Authentication backends — AxesStandaloneBackend first so it can block
# locked-out logins before the normal ModelBackend runs.
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Database - configured per environment
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# =============================================================================
# Sefaria Status Monitoring Configuration
# =============================================================================

# Services to monitor
MONITORED_SERVICES = [
    {
        "name": "sefaria.org",
        "url": env("SEFARIA_HEALTH_URL", default="https://www.sefaria.org/healthz"),
        "method": "GET",
        "expected_status": 200,
        "timeout": 10,
        "follow_redirects": True,
        "failure_threshold": 2,
    },
    {
        "name": "MCP Server",
        "url": env("MCP_HEALTH_URL", default="https://mcp.sefaria.org/healthz"),
        "method": "GET",
        "expected_status": 200,
        "timeout": 20,
        # Higher threshold: this origin is briefly restarted on a daily
        # schedule (~07:2x UTC) and returns Cloudflare 521 for a few minutes.
        # Requiring 4 consecutive failed cycles absorbs that routine restart
        # while still catching a genuine sustained (>~4 min) outage.
        "failure_threshold": 4,
    },
    {
        "name": "AI Chatbot",
        "url": env("AI_CHATBOT_HEALTH_URL", default="https://chat.sefaria.org/api/health"),
        "method": "GET",
        "expected_status": 200,
        "timeout": 20,
        # Same daily ~07:2x UTC origin restart as MCP Server — see above.
        "failure_threshold": 4,
    },
    {
        "name": "Linker",
        "url": env("LINKER_HEALTH_URL", default="https://www.sefaria.org/api/find-refs"),
        "method": "POST",
        "expected_status": 202,
        "timeout": 20,
        "check_type": "async_two_phase",
        "request_body": {"text": {"title": "", "body": "Job 1:1"}},
        "async_verification": {
            "base_url": "https://www.sefaria.org/api/async/",
            "max_poll_attempts": 10,
            "poll_interval": 1,
        },
        "failure_threshold": 3,
        # The two-phase Linker check is intrinsically slower (~1.2s) than a
        # plain ping, so it gets a higher "degraded" threshold than the default.
        "degraded_threshold_ms": 4000,
    },
]

# Check interval in seconds
HEALTH_CHECK_INTERVAL = env.int("HEALTH_CHECK_INTERVAL", default=60)

# Retry configuration
HEALTH_CHECK_RETRIES = env.int("HEALTH_CHECK_RETRIES", default=3)
HEALTH_CHECK_RETRY_DELAY = env.int("HEALTH_CHECK_RETRY_DELAY", default=10)

# Consecutive failure threshold (default for services without per-service config)
ALERT_AFTER_CONSECUTIVE_FAILURES = env.int("ALERT_AFTER_CONSECUTIVE_FAILURES", default=2)

# A service that is up but whose latest response time exceeds this many
# milliseconds is shown as "Degraded Performance" on the status page. This is
# a page-only signal (it does NOT send a Slack alert). Override per service
# with "degraded_threshold_ms" in MONITORED_SERVICES.
DEGRADED_RESPONSE_MS = env.int("DEGRADED_RESPONSE_MS", default=2000)

# Slack configuration
SLACK_WEBHOOK_URL = env("SLACK_WEBHOOK_URL", default="")
SLACK_CHANNEL = env("SLACK_CHANNEL", default="sefaria-down")

# Status page URL (for Slack messages)
STATUS_PAGE_URL = env("STATUS_PAGE_URL", default="https://status.sefaria.org")

# Data retention (days)
HEALTH_CHECK_RETENTION_DAYS = env.int("HEALTH_CHECK_RETENTION_DAYS", default=60)

# =============================================================================
# Admin login protection (django-axes)
# =============================================================================
# Lock an account after this many failed admin logins, then cool off.
AXES_FAILURE_LIMIT = env.int("AXES_FAILURE_LIMIT", default=5)
AXES_COOLOFF_TIME = env.int("AXES_COOLOFF_HOURS", default=1)  # hours
# Lock by username (not IP): behind a reverse proxy every request shares the
# proxy's IP, so IP-based lockout would be unreliable. A successful login
# clears the user's failure count. Reset manually with `manage.py axes_reset`.
AXES_LOCKOUT_PARAMETERS = ["username"]
AXES_RESET_ON_SUCCESS = True

# Silence axes.W006 (it recommends adding 'ip_address'). We deliberately lock
# by username only: behind the reverse proxy a client IP isn't reliable, and
# IP-based lockout could lock out everyone at once. Username-only is also
# immune to the User-Agent/Cookie rotation bypass W006 warns about.
SILENCED_SYSTEM_CHECKS = ["axes.W006"]
