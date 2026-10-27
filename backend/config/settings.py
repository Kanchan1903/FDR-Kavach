import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"
DEMO_MODE = os.environ.get("DEMO_MODE", "false").lower() == "true"
if not SECRET_KEY or (not DEBUG and (SECRET_KEY.startswith("replace-with-") or len(SECRET_KEY) < 50)):
    raise ImproperlyConfigured("Set a unique DJANGO_SECRET_KEY of at least 50 characters for production.")
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,api").split(",")
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "security",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "security.middleware.SecurityHeadersMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
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
            ]
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
DATABASES = {"default": dj_database_url.config(default="sqlite:///db.sqlite3", conn_max_age=60)}
AUTH_USER_MODEL = "security.User"
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
MEDIA_ROOT = os.environ.get("MEDIA_ROOT", str(BASE_DIR / "media"))
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"
SESSION_COOKIE_AGE = 8 * 60 * 60
SESSION_SAVE_EVERY_REQUEST = True
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = os.environ.get("CSRF_COOKIE_SECURE", "0") == "1"
CSRF_TRUSTED_ORIGINS = os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "http://localhost:3000").split(",")
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
SECURE_SSL_REDIRECT = os.environ.get("SECURE_SSL_REDIRECT", "0") == "1"
CORS_ALLOWED_ORIGINS = [os.environ.get("FRONTEND_ORIGIN", "http://localhost:3000")]
CORS_ALLOW_CREDENTIALS = True
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["security.authentication.SessionCsrfAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_THROTTLE_CLASSES": ["security.throttling.LoginRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"login": "10/min"},
    "EXCEPTION_HANDLER": "security.exceptions.safe_exception_handler",
}
LOGIN_MAX_ATTEMPTS = int(os.environ.get("LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_LOCKOUT_MINUTES = int(os.environ.get("LOGIN_LOCKOUT_MINUTES", "15"))
INSTITUTION_MFA_REQUIRED = os.environ.get("INSTITUTION_MFA_REQUIRED", "0") == "1"
TOTP_ISSUER = os.environ.get("TOTP_ISSUER", "FDR Kavach")
MFA_ENCRYPTION_KEY = os.environ.get("MFA_ENCRYPTION_KEY", "")
DOCUMENT_ENCRYPTION_KEY = os.environ.get("DOCUMENT_ENCRYPTION_KEY", "")
if not DEBUG:
    if DEMO_MODE:
        raise ImproperlyConfigured("DEMO_MODE must be disabled outside development.")
    if not MFA_ENCRYPTION_KEY or MFA_ENCRYPTION_KEY.startswith("replace-with-"):
        raise ImproperlyConfigured("Set MFA_ENCRYPTION_KEY to a stable secret in production.")
    if not DOCUMENT_ENCRYPTION_KEY or DOCUMENT_ENCRYPTION_KEY.startswith("replace-with-"):
        raise ImproperlyConfigured("Set DOCUMENT_ENCRYPTION_KEY to a stable secret in production.")
    if os.environ.get("SESSION_COOKIE_SECURE") != "1" or os.environ.get("CSRF_COOKIE_SECURE") != "1":
        raise ImproperlyConfigured("Production session and CSRF cookies must use Secure=true.")
    if os.environ.get("SECURE_SSL_REDIRECT") != "1":
        raise ImproperlyConfigured("SECURE_SSL_REDIRECT must be enabled in production.")
    if not os.environ.get("FRONTEND_ORIGIN", "").startswith("https://"):
        raise ImproperlyConfigured("FRONTEND_ORIGIN must use HTTPS in production.")
    trusted_origins = os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if any(not origin.startswith("https://") for origin in trusted_origins):
        raise ImproperlyConfigured("Every trusted CSRF origin must use HTTPS in production.")
CELERY_BROKER_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_BEAT_SCHEDULE = {
    "dispatch-case-reminders-and-escalations-every-15-minutes": {
        "task": "security.tasks.dispatch_case_alerts",
        "schedule": 900.0,
    },
    "calculate-daily-coverage": {"task": "security.tasks.calculate_daily_coverage", "schedule": 86400.0},
}
USE_REDIS_CACHE = os.environ.get("DJANGO_USE_REDIS_CACHE", "0" if DEBUG else "1") == "1"
CACHES = (
    {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": CELERY_BROKER_URL,
        }
    }
    if USE_REDIS_CACHE
    else {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
)
