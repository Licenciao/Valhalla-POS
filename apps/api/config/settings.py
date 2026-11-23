import os
import sys
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY debe configurarse mediante el entorno.")

DEBUG = os.environ.get("DJANGO_DEBUG", "").strip().lower() in {"1", "true", "yes"}
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,192.168.1.70,0.0.0.0,*").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "corsheaders",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "rest_framework",
    "system.apps.SystemConfig",
    "catalog.apps.CatalogConfig",
    "devices.apps.DevicesConfig",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "system.middleware.RequestIdMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "system.middleware.SessionTimeoutMiddleware",
]

CORS_ALLOW_ALL_ORIGINS = DEBUG
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://192.168.1.70:8000",
    "http://0.0.0.0:8000",
    "http://127.0.0.1:5500",
    "http://localhost:5500",
    "http://127.0.0.1:3000",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = []
WSGI_APPLICATION = "config.wsgi.application"

_test_sqlite_enabled = os.environ.get("DJANGO_TEST_SQLITE") == "1"
_sqlite_path = os.environ.get("DJANGO_SQLITE_PATH")
if _sqlite_path:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": _sqlite_path}}
elif _test_sqlite_enabled:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
else:
    postgres_settings = {
        "NAME": os.environ.get("POSTGRES_DB"),
        "USER": os.environ.get("POSTGRES_USER"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD"),
        "HOST": os.environ.get("POSTGRES_HOST"),
        "PORT": os.environ.get("POSTGRES_PORT"),
    }
    missing_postgres_settings = [
        name for name, value in postgres_settings.items() if not value
    ]
    if missing_postgres_settings:
        missing = ", ".join(missing_postgres_settings)
        raise ImproperlyConfigured(
            f"Configuración PostgreSQL faltante: {missing}. "
            "La API no tiene fallback a otra base de datos."
        )
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            **postgres_settings,
            "CONN_MAX_AGE": 60,
            "CONN_HEALTH_CHECKS": True,
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "es-mx"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_AGE = 30 * 60
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Strict"
SESSION_SAVE_EVERY_REQUEST = True
CSRF_USE_SESSIONS = True
CSRF_FAILURE_VIEW = "system.errors.csrf_failure"

SESSION_IDLE_TIMEOUT_SECONDS = 30 * 60
SESSION_ABSOLUTE_TIMEOUT_SECONDS = 8 * 60 * 60
API_VERSION = os.environ.get("API_VERSION", "v1")

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "EXCEPTION_HANDLER": "system.errors.api_exception_handler",
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "system.authentication.SessionAuthentication",
    ],
}
