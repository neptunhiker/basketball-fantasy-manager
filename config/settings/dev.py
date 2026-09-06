"""Local development settings."""

from .base import *  # noqa: F403
from .base import BASE_DIR, env

DEBUG = True
SECRET_KEY = env("SECRET_KEY", default="dev-only-insecure-key-do-not-use-in-production")
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "[::1]"]

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://postgres:postgres@localhost:5433/crossover",
    )
}
DATABASES["default"]["ATOMIC_REQUESTS"] = True

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

# The Tailwind CLI writes here; keep it out of version control.
TAILWIND_OUTPUT = BASE_DIR / "static" / "css" / "app.css"
