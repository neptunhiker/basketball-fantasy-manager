"""Production settings (Fly.io)."""

from .base import *  # noqa: F403
from .base import env

DEBUG = False

# Fly gives every app a *.fly.dev hostname; extra hosts come from the environment.
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])
if fly_app := env("FLY_APP_NAME", default=""):
    ALLOWED_HOSTS.append(f"{fly_app}.fly.dev")

CSRF_TRUSTED_ORIGINS = [f"https://{host}" for host in ALLOWED_HOSTS if not host.startswith(".")]

# Fly terminates TLS at the edge and forwards this header.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
# Fly and Docker call the probe internally over plain HTTP; without this it would
# be answered with a 301 and the machine would never be marked healthy.
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"

EMAIL_BACKEND = "anymail.backends.resend.EmailBackend"
ANYMAIL = {"RESEND_API_KEY": env("RESEND_API_KEY")}

CONN_MAX_AGE = 60
DATABASES["default"]["CONN_MAX_AGE"] = CONN_MAX_AGE  # noqa: F405
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True  # noqa: F405
