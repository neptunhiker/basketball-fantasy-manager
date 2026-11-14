"""Local development settings."""

import os

from .base import *  # noqa: F403
from .base import BASE_DIR, DATABASE_OPTIONS, env

DEBUG = True
ENVIRONMENT = env("ENVIRONMENT", default="development").strip().lower()
ALLOW_DEMO_DATA = env.bool("ALLOW_DEMO_DATA", default=True)
# A laptop never loads the schedule itself unless told to: base derived the
# default from an ENVIRONMENT that defaults to production.
NBA_SCHEDULE_ENABLED = env.bool("NBA_SCHEDULE_ENABLED", default=ENVIRONMENT == "production")
SECRET_KEY = env("SECRET_KEY", default="dev-only-insecure-key-do-not-use-in-production")
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "[::1]"]

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://postgres:postgres@localhost:5433/crossover",
    )
}
DATABASES["default"]["ATOMIC_REQUESTS"] = True
DATABASES["default"]["OPTIONS"] = DATABASE_OPTIONS

# Emails are printed to the console instead of being delivered.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# Run background tasks synchronously so there is no worker to babysit locally.
Q_CLUSTER = {**globals()["Q_CLUSTER"], "sync": True}

# Django's staticfiles app serves assets while DEBUG is on, so WhiteNoise would
# only warn about the missing (not-yet-collected) STATIC_ROOT.
MIDDLEWARE = [m for m in globals()["MIDDLEWARE"] if "whitenoise" not in m]

# Serve static files straight from disk; no collectstatic needed while developing.
STORAGES = {
    **globals()["STORAGES"],
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

INTERNAL_IPS = ["127.0.0.1"]

# Django Debug Toolbar: queries, templates and timings per page, locally only.
# Kept out of test runs, which use these settings too: it would refuse to load
# under pytest, and its panels would muddy the query-count assertions.
# Placed after apps.perf's middleware, so local timings include the toolbar's
# own overhead -- compare production numbers with production numbers.
TESTING = "PYTEST_VERSION" in os.environ
if not TESTING and env.bool("DEBUG_TOOLBAR", default=True):
    INSTALLED_APPS = [*globals()["INSTALLED_APPS"], "debug_toolbar"]
    MIDDLEWARE = [
        MIDDLEWARE[0],
        "debug_toolbar.middleware.DebugToolbarMiddleware",
        *MIDDLEWARE[1:],
    ]

# The Tailwind CLI writes here; keep it out of version control.
TAILWIND_OUTPUT = BASE_DIR / "static" / "css" / "app.css"
