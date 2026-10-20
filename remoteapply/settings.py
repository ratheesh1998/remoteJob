"""RemoteApply settings. Single-user app: no auth, no sessions, no admin."""
import os
import secrets
from pathlib import Path
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def env_list(name, default=""):
    return [v.strip() for v in os.environ.get(name, default).split(",") if v.strip()]


DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _persisted_secret(env_name, filename, factory):
    """Env value wins; otherwise generate once and keep it in DATA_DIR (mode 600)."""
    value = os.environ.get(env_name, "").strip()
    if value:
        return value
    path = DATA_DIR / filename
    if path.exists():
        return path.read_text().strip()
    value = factory()
    path.write_text(value)
    os.chmod(path, 0o600)
    return value


SECRET_KEY = _persisted_secret("DJANGO_SECRET_KEY", ".secret_key", lambda: secrets.token_urlsafe(50))
DEBUG = env_bool("DEBUG", False)

# Single-user app with no login: only local hostnames are allowed by default.
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", "127.0.0.1,localhost,[::1]")
LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1", "[::1]"}

INSTALLED_APPS = [
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "cv",
    "jobs",
    "applications",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.SMTPGateMiddleware",
]

MESSAGE_STORAGE = "django.contrib.messages.storage.cookie.CookieStorage"
ROOT_URLCONF = "remoteapply.urls"
WSGI_APPLICATION = "remoteapply.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.app_context",
            ],
        },
    },
]


def _database():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": DATA_DIR / "db.sqlite3",
                "OPTIONS": {"timeout": 30}}
    parsed = urlparse(url)
    if parsed.scheme.startswith("sqlite"):
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": parsed.path.lstrip("/") or DATA_DIR / "db.sqlite3"}
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": parsed.path.lstrip("/"),
        "USER": unquote(parsed.username or ""),
        "PASSWORD": unquote(parsed.password or ""),
        "HOST": parsed.hostname or "localhost",
        "PORT": parsed.port or 5432,
    }


DATABASES = {"default": _database()}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TIME_ZONE", "UTC")
USE_I18N = False
USE_TZ = True

STATIC_URL = "/static/"
MEDIA_ROOT = DATA_DIR / "media"
MEDIA_ROOT.mkdir(parents=True, exist_ok=True)

DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024

# --- Celery ---
REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_ALWAYS_EAGER = env_bool("CELERY_TASK_ALWAYS_EAGER", False)
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_TRACK_STARTED = True
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_TIMEZONE = TIME_ZONE
# A send scheduled with an ETA must not be redelivered early/duplicated by the broker.
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 6 * 3600}
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TASK_ACKS_LATE = False

# --- LLM (Anthropic) ---
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-opus-5-5").strip()

# --- Job sources ---
JOB_SOURCE_TIMEOUT = int(os.environ.get("JOB_SOURCE_TIMEOUT", "20"))
JOB_SOURCE_RETRIES = int(os.environ.get("JOB_SOURCE_RETRIES", "2"))
MIN_MATCH_SCORE_TO_KEEP = int(os.environ.get("MIN_MATCH_SCORE_TO_KEEP", "10"))
ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID", "").strip()
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY", "").strip()
ADZUNA_COUNTRY = os.environ.get("ADZUNA_COUNTRY", "us").strip()
JSEARCH_API_KEY = os.environ.get("JSEARCH_API_KEY", "").strip()

# --- Sending ---
SEND_DELAY_MIN = int(os.environ.get("SEND_DELAY_MIN", "20"))
SEND_DELAY_MAX = int(os.environ.get("SEND_DELAY_MAX", "60"))
SEND_MAX_RETRIES = 2
SMTP_TIMEOUT = int(os.environ.get("SMTP_TIMEOUT", "30"))
CV_MAX_BYTES = 5 * 1024 * 1024

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
}
