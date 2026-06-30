"""
Production settings for Coolify deployment.
"""
import dj_database_url

from .base import *  # noqa: F401, F403

DEBUG = False

# Require a real SECRET_KEY in production — never fall back to the insecure
# development default (base.py). Missing/empty raises ImproperlyConfigured at
# startup, which is what we want: fail loudly rather than run with a known key.
# Generate one with: python -c "from django.core.management.utils import get_random_secret_key as g; print(g())"
SECRET_KEY = env("SECRET_KEY")  # noqa: F405

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["status.sefaria.org"])  # noqa: F405

# Always permit loopback so the container HEALTHCHECK (curl http://localhost
# :8000/healthz) passes Django host validation no matter how the operator set
# ALLOWED_HOSTS. Without this, a localhost probe returns 400 DisallowedHost and
# the web container is marked unhealthy, which fails the whole deploy.
for _loopback in ("localhost", "127.0.0.1"):
    if _loopback not in ALLOWED_HOSTS:
        ALLOWED_HOSTS.append(_loopback)

# PostgreSQL via DATABASE_URL
DATABASES = {
    "default": dj_database_url.config(
        default="postgres://localhost:5432/sefaria_status",
        conn_max_age=600,
        conn_health_checks=True,
    )
}

# Security settings for production
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Proxy configuration
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Redirect any plain-HTTP request to HTTPS. Safe behind the TLS-terminating
# proxy because SECURE_PROXY_SSL_HEADER lets Django recognize already-secure
# requests (so no redirect loop). The container's loopback /healthz probe is
# exempted so it isn't bounced to https on an internal http call.
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]

# Send the Referer only to same-origin destinations (Django default since 3.1,
# set explicitly for clarity).
SECURE_REFERRER_POLICY = "same-origin"

# CSRF settings
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=True)
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=True)

# CSRF Trusted Origins (required for Django 4.0+ over HTTPS)
# We handle both status.sefaria.org and coolify dev domains automatically
CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS",
    default=[f"https://{host}" for host in ALLOWED_HOSTS if host not in ["localhost", "127.0.0.1"]]
)

# Logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "monitoring": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
