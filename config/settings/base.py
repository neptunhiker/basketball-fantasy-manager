"""Settings shared by every environment."""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])
ENVIRONMENT = env("ENVIRONMENT", default="production").strip().lower()
ALLOW_DEMO_DATA = env.bool("ALLOW_DEMO_DATA", default=False)

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "django_htmx",
    "django_q",
    "anymail",
]

LOCAL_APPS = [
    "apps.accounts",
    "apps.core",
    "apps.nba",
    "apps.fantasy",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.accounts.middleware.UserLanguageMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.site",
            ],
        },
    },
]

DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["ATOMIC_REQUESTS"] = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 8},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# How long an invitation link stays valid.
INVITATION_TIMEOUT_DAYS = env.int("INVITATION_TIMEOUT_DAYS", default=14)
PASSWORD_RESET_TIMEOUT = 60 * 60 * 24 * 3  # 3 days

LANGUAGE_CODE = "en"
LANGUAGES = (
    ("en", "English"),
    ("de", "German"),
)
LOCALE_PATHS = [BASE_DIR / "locale"]
# Day-first dates and a 24-hour clock in English too; see config/formats/en.
FORMAT_MODULE_PATH = ["config.formats"]
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="noreply@example.com")
SERVER_EMAIL = DEFAULT_FROM_EMAIL

Q_CLUSTER = {
    "name": "crossover",
    "orm": "default",
    "workers": env.int("Q_WORKERS", default=2),
    "timeout": 300,
    "retry": 360,
    "max_attempts": 3,
    "save_limit": 250,
    "catch_up": False,
    "label": "Hintergrundaufgaben",
}

SITE_NAME = "Pengi Fantasy Manager"

# The basketball.de login for the daily scheduled import. Empty disables it;
# the manual import in the app asks for a login and never uses these.
BBDE_USERNAME = env("BBDE_USERNAME", default="")
BBDE_PASSWORD = env("BBDE_PASSWORD", default="")

RAPID_API_KEY = env("RAPID_API_KEY", default="")
RAPID_API_HOST = env("RAPID_API_HOST", default="api-basketball-nba.p.rapidapi.com")
RAPID_API_INJURIES_URL = env(
    "RAPID_API_INJURIES_URL",
    default="https://api-basketball-nba.p.rapidapi.com/nba-injuries",
)
RAPID_API_TIMEOUT = env.int("RAPID_API_TIMEOUT", default=15)
# Requests a day, on one shared tally: everyone may call while none has been
# made today, a superuser until the second limit is reached.
RAPID_API_DAILY_LIMIT = 1
RAPID_API_SUPERUSER_DAILY_LIMIT = 5
# The subscription has 90 requests a month, shared by the injury button and the
# NBA schedule import. Both stop once RapidAPI reports fewer left than this.
RAPID_API_MONTHLY_RESERVE = 10

# RotoWire player news, on the same RapidAPI account as the injury feed above.
# The free plan stops at 500 requests a month; the schedule makes three a day,
# and the daily limit leaves five for manual runs.
ROTOWIRE_HOST = env("ROTOWIRE_HOST", default="rotowire-fantasy-sports-api.p.rapidapi.com")
ROTOWIRE_NEWS_URL = env(
    "ROTOWIRE_NEWS_URL",
    default="https://rotowire-fantasy-sports-api.p.rapidapi.com/api/v1/news/nba",
)
ROTOWIRE_DAILY_LIMIT = 8
# Off stops every request, scheduled or by hand: staging sets it to false so
# the shared key's requests are left to production.
ROTOWIRE_ENABLED = env.bool("ROTOWIRE_ENABLED", default=True)
# Pulls happen from two weeks before a season starts -- preseason injuries and
# signings -- to ten weeks after it ends, through the Finals. Outside that, and
# with no season set up at all, the schedule skips.
ROTOWIRE_DAYS_BEFORE_SEASON = 14
ROTOWIRE_DAYS_AFTER_SEASON = 70
# The schedule stops once RapidAPI reports fewer requests left this month,
# keeping the rest for a pull by hand.
ROTOWIRE_MONTHLY_RESERVE = 20

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "{levelname} {name} {message}", "style": "{"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "handlers": ["console"], "propagate": False},
    },
}
