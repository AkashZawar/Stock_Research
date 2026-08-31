"""Django settings for the stockdesk project.

Key points:
- ``INSTALLED_APPS`` lists the shared ``core`` app plus one app per workspace
  tab (stock_analysis, agent_desk, recommendations, market_monitor,
  etf_analysis, mutual_funds, ipo).
- Static files are served from ``public/`` (``STATICFILES_DIRS``) in dev.
- No database: the app is read-only over live upstreams. Timezone Asia/Kolkata.
- Secret key, debug, and allowed hosts read from environment variables with
  dev-friendly defaults.
"""
import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-stock-research-desk")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "core",
    "stock_analysis",
    "agent_desk",
    "recommendations",
    "market_monitor",
    "etf_analysis",
    "mutual_funds",
    "ipo",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "stockdesk.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [],
        },
    }
]

WSGI_APPLICATION = "stockdesk.wsgi.application"
ASGI_APPLICATION = "stockdesk.asgi.application"

# No database. Every tab fetches live from its upstream on request and caches
# in memory only, so nothing survives a request and there is nothing to store.
# Declaring it empty rather than leaving an unused SQLite file configured keeps
# a stray db file from being created, committed, and then served as stale data.
DATABASES = {}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "public"]
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
