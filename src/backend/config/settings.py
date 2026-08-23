import math
import os
from pathlib import Path
from typing import Any

import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")


def _exact_environment_boolean(name: str, *, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    if value == "True":
        return True
    if value == "False":
        return False
    raise ImproperlyConfigured(f"{name} must be True or False")


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME"),
        "USER": env("DB_USER"),
        "PASSWORD": env("DB_PASSWORD"),
        "HOST": env("DB_HOST"),
        "PORT": env("DB_PORT"),
        "TEST": {"NAME": env("TEST_DB_NAME", default=None)},
    }
}
DEBUG = env.bool("DEBUG", default=False)
DEFAULT_EXCEPTION_REPORTER_FILTER = "commerce.views.CartExceptionReporterFilter"
YANDEX_METRIKA_COUNTER_ID = 111239706

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=[])
PUBLIC_DOMAIN = env("PUBLIC_DOMAIN", default="")
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "commerce.apps.CommerceConfig",
    "feature_flags.apps.FeatureFlagsConfig",
    "ingestion.apps.IngestionConfig",
    "picflow.apps.PicflowConfig",
    "processing.apps.ProcessingConfig",
    "selfie_search.apps.SelfieSearchConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "config.metrics.HttpMetricsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "selfie_search.middleware.PublicSelfieBearerProtectionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "config.context_processors.analytics",
                "ingestion.context_processors.photographer_navigation",
            ],
        },
    },
]

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES: dict[str, dict[str, Any]] = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

PRIVATE_MEDIA_S3_BUCKET = env("PRIVATE_MEDIA_S3_BUCKET", default="")
PRIVATE_MEDIA_S3_ACCESS_KEY_ID = env("PRIVATE_MEDIA_S3_ACCESS_KEY_ID", default="")
PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY = env("PRIVATE_MEDIA_S3_SECRET_ACCESS_KEY", default="")
PRIVATE_MEDIA_S3_ENDPOINT_URL = env(
    "MEDIA_S3_ENDPOINT_URL",
    default="https://storage.yandexcloud.net",
)
PRIVATE_MEDIA_S3_REGION = env("MEDIA_S3_REGION", default="ru-central1")
PRIVATE_MEDIA_ALLOWED_ORIGINS = [
    origin.strip() for origin in env.list("PRIVATE_MEDIA_ALLOWED_ORIGINS", default=[])
]
COMMERCE_SUPPORT_CONTACT = env("COMMERCE_SUPPORT_CONTACT", default="")
COMMERCE_PUBLIC_ORIGIN = env("COMMERCE_PUBLIC_ORIGIN", default="")
COMMERCE_PAYMENT_GATEWAY_FACTORY = env("COMMERCE_PAYMENT_GATEWAY_FACTORY", default="")
COMMERCE_EMAIL_SENDER_FACTORY = env("COMMERCE_EMAIL_SENDER_FACTORY", default="")
COMMERCE_WORKER_FACTORY = env("COMMERCE_WORKER_FACTORY", default="")
COMMERCE_SMTP_HOST = env("COMMERCE_SMTP_HOST", default="")
COMMERCE_SMTP_PORT = env.int("COMMERCE_SMTP_PORT", default=25)
COMMERCE_WORKER_ENABLED = env.bool("COMMERCE_WORKER_ENABLED", default=False)
COMMERCE_EMAIL_FROM_ADDRESS = env("COMMERCE_EMAIL_FROM_ADDRESS", default="")
COMMERCE_POSTBOX_API_KEY_ID = env("COMMERCE_POSTBOX_API_KEY_ID", default="")
COMMERCE_POSTBOX_API_KEY_SECRET = env("COMMERCE_POSTBOX_API_KEY_SECRET", default="")
COMMERCE_ORDER_ACCESS_SIGNING_SECRET = env("COMMERCE_ORDER_ACCESS_SIGNING_SECRET", default="")
COMMERCE_WORKER_HEALTH_MAX_READY_AGE_SECONDS = env.int(
    "COMMERCE_WORKER_HEALTH_MAX_READY_AGE_SECONDS", default=300
)

PHOTO_UPLOAD_ENABLED = env.bool("PHOTO_UPLOAD_ENABLED", default=False)
PHOTO_UPLOAD_MAX_FILES = env.int("PHOTO_UPLOAD_MAX_FILES", default=10_000)
PHOTO_UPLOAD_MAX_FILE_BYTES = env.int("PHOTO_UPLOAD_MAX_FILE_BYTES", default=50 * 1024 * 1024)
PHOTO_UPLOAD_REGISTRATION_CHUNK = env.int("PHOTO_UPLOAD_REGISTRATION_CHUNK", default=100)
PHOTO_UPLOAD_CONCURRENCY = env.int("PHOTO_UPLOAD_CONCURRENCY", default=4)
PHOTO_UPLOAD_GRANT_TTL_SECONDS = env.int("PHOTO_UPLOAD_GRANT_TTL_SECONDS", default=600)
PHOTO_UPLOAD_STALE_AFTER_SECONDS = env.int("PHOTO_UPLOAD_STALE_AFTER_SECONDS", default=86_400)

# Disabled by default: the private worker API also denies every request unless its separate
# environment-provided bearer token is present.  This token is never shared with Django, users,
# or object storage and must never be logged or persisted.
PHOTO_PROCESSING_ENABLED = _exact_environment_boolean("PHOTO_PROCESSING_ENABLED")
PHOTO_PROCESSING_FACE_ENABLED = _exact_environment_boolean("PHOTO_PROCESSING_FACE_ENABLED")
PHOTO_PROCESSING_PREVIEW_ENABLED = _exact_environment_boolean("PHOTO_PROCESSING_PREVIEW_ENABLED")
PHOTO_PROCESSING_WORKER_TOKEN = env("PHOTO_PROCESSING_WORKER_TOKEN", default="")
PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS = env.int(
    "PHOTO_PROCESSING_DOWNLOAD_TTL_SECONDS", default=120
)
PHOTO_PROCESSING_MAX_REQUEST_BYTES = env.int(
    "PHOTO_PROCESSING_MAX_REQUEST_BYTES", default=384 * 1024
)

SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED = _exact_environment_boolean(
    "SELFIE_SEARCH_CLUSTER_EXPANSION_ENABLED"
)
SELFIE_SEARCH_MAX_UPLOAD_BYTES = env.int("SELFIE_SEARCH_MAX_UPLOAD_BYTES", default=20 * 1024 * 1024)
SELFIE_SEARCH_MAX_PIXELS = env.int("SELFIE_SEARCH_MAX_PIXELS", default=25_000_000)
SELFIE_SEARCH_DOWNLOAD_TTL_SECONDS = env.int("SELFIE_SEARCH_DOWNLOAD_TTL_SECONDS", default=120)
ADAFACE_LOCAL_EXPERIMENT_ENABLED = _exact_environment_boolean("ADAFACE_LOCAL_EXPERIMENT_ENABLED")
if ADAFACE_LOCAL_EXPERIMENT_ENABLED:
    if not DEBUG:
        raise ImproperlyConfigured("AdaFace local experiment requires DEBUG=True")
    ADAFACE_LOCAL_CANARY_LIMIT = env.int("ADAFACE_LOCAL_CANARY_LIMIT", default=0)
    if ADAFACE_LOCAL_CANARY_LIMIT not in (0, 100):
        raise ImproperlyConfigured("ADAFACE_LOCAL_CANARY_LIMIT must be 0 or 100")
    ADAFACE_LOCAL_COSINE_DISTANCE_THRESHOLD = env.float("ADAFACE_LOCAL_COSINE_DISTANCE_THRESHOLD")
    if (
        not math.isfinite(ADAFACE_LOCAL_COSINE_DISTANCE_THRESHOLD)
        or not 0.0 <= ADAFACE_LOCAL_COSINE_DISTANCE_THRESHOLD <= 2.0
        or ADAFACE_LOCAL_COSINE_DISTANCE_THRESHOLD == 0.363
    ):
        raise ImproperlyConfigured(
            "ADAFACE_LOCAL_COSINE_DISTANCE_THRESHOLD must be finite and must not be 0.363"
        )
    SELFIE_SEARCH_EMBEDDING_MODEL = "adaface-ir18-webface4m"
    SELFIE_SEARCH_EMBEDDING_DIMENSIONS = 512
    SELFIE_SEARCH_COSINE_DISTANCE_THRESHOLD = ADAFACE_LOCAL_COSINE_DISTANCE_THRESHOLD
    PHOTO_PROCESSING_MAX_REQUEST_BYTES = 384 * 1024
else:
    ADAFACE_LOCAL_CANARY_LIMIT = 0
    ADAFACE_LOCAL_COSINE_DISTANCE_THRESHOLD = None
    SELFIE_SEARCH_EMBEDDING_MODEL = env("SELFIE_SEARCH_EMBEDDING_MODEL", default="sface")
    SELFIE_SEARCH_EMBEDDING_DIMENSIONS = env.int("SELFIE_SEARCH_EMBEDDING_DIMENSIONS", default=128)
    SELFIE_SEARCH_COSINE_DISTANCE_THRESHOLD = env.float(
        "SELFIE_SEARCH_COSINE_DISTANCE_THRESHOLD", default=0.363
    )
SELFIE_SEARCH_TEMPORARY_PREFIX = env("SELFIE_SEARCH_TEMPORARY_PREFIX", default="selfie-search/")
SELFIE_SEARCH_LIFECYCLE_MAX_AGE_HOURS = env.int("SELFIE_SEARCH_LIFECYCLE_MAX_AGE_HOURS", default=24)
if not ADAFACE_LOCAL_EXPERIMENT_ENABLED and (
    SELFIE_SEARCH_MAX_UPLOAD_BYTES != 20 * 1024 * 1024
    or SELFIE_SEARCH_MAX_PIXELS != 25_000_000
    or SELFIE_SEARCH_DOWNLOAD_TTL_SECONDS != 120
    or SELFIE_SEARCH_EMBEDDING_MODEL != "sface"
    or SELFIE_SEARCH_EMBEDDING_DIMENSIONS != 128
    or SELFIE_SEARCH_COSINE_DISTANCE_THRESHOLD != 0.363
    or SELFIE_SEARCH_TEMPORARY_PREFIX != "selfie-search/"
    or SELFIE_SEARCH_LIFECYCLE_MAX_AGE_HOURS != 24
):
    raise ImproperlyConfigured("Selfie-search settings do not match the approved contract")

SELFIE_FEEDBACK_ENABLED = _exact_environment_boolean("SELFIE_FEEDBACK_ENABLED")
if SELFIE_FEEDBACK_ENABLED:
    SELFIE_FEEDBACK_S3_BUCKET = env("SELFIE_FEEDBACK_S3_BUCKET", default="")
    SELFIE_FEEDBACK_S3_ACCESS_KEY_ID = env("SELFIE_FEEDBACK_S3_ACCESS_KEY_ID", default="")
    SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY = env("SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY", default="")
    SELFIE_FEEDBACK_S3_ENDPOINT_URL = env(
        "SELFIE_FEEDBACK_S3_ENDPOINT_URL", default="https://storage.yandexcloud.net"
    )
    SELFIE_FEEDBACK_S3_REGION = env("SELFIE_FEEDBACK_S3_REGION", default="ru-central1")
    SELFIE_FEEDBACK_KMS_KEY_ID = env("SELFIE_FEEDBACK_KMS_KEY_ID", default="")
    SELFIE_FEEDBACK_MAX_UPLOAD_BYTES = env.int(
        "SELFIE_FEEDBACK_MAX_UPLOAD_BYTES", default=20 * 1024 * 1024
    )
    SELFIE_FEEDBACK_DOWNLOAD_TTL_SECONDS = env.int(
        "SELFIE_FEEDBACK_DOWNLOAD_TTL_SECONDS", default=60
    )
    required_values = {
        "SELFIE_FEEDBACK_S3_BUCKET": SELFIE_FEEDBACK_S3_BUCKET,
        "SELFIE_FEEDBACK_S3_ACCESS_KEY_ID": SELFIE_FEEDBACK_S3_ACCESS_KEY_ID,
        "SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY": SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY,
        "SELFIE_FEEDBACK_KMS_KEY_ID": SELFIE_FEEDBACK_KMS_KEY_ID,
    }
    if not all(isinstance(value, str) and value.strip() for value in required_values.values()):
        raise ImproperlyConfigured(
            "Selfie-feedback requires dedicated bucket credentials and KMS key"
        )
    if SELFIE_FEEDBACK_S3_BUCKET == PRIVATE_MEDIA_S3_BUCKET:
        raise ImproperlyConfigured("Selfie-feedback bucket must be separate from private media")
    if (
        SELFIE_FEEDBACK_MAX_UPLOAD_BYTES != 20 * 1024 * 1024
        or SELFIE_FEEDBACK_DOWNLOAD_TTL_SECONDS != 60
        or SELFIE_FEEDBACK_S3_ENDPOINT_URL != "https://storage.yandexcloud.net"
        or SELFIE_FEEDBACK_S3_REGION != "ru-central1"
    ):
        raise ImproperlyConfigured("Selfie-feedback settings do not match the approved contract")
else:
    SELFIE_FEEDBACK_S3_BUCKET = ""
    SELFIE_FEEDBACK_S3_ACCESS_KEY_ID = ""
    SELFIE_FEEDBACK_S3_SECRET_ACCESS_KEY = ""
    SELFIE_FEEDBACK_S3_ENDPOINT_URL = "https://storage.yandexcloud.net"
    SELFIE_FEEDBACK_S3_REGION = "ru-central1"
    SELFIE_FEEDBACK_KMS_KEY_ID = ""
    SELFIE_FEEDBACK_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
    SELFIE_FEEDBACK_DOWNLOAD_TTL_SECONDS = 60

LOGIN_URL = "photographer_login"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "selfie_console": {
            "class": "logging.StreamHandler",
            "level": "INFO",
        }
    },
    "loggers": {
        "selfie_search": {
            "handlers": ["selfie_console"],
            "level": "INFO",
            "propagate": False,
        }
    },
}

if env("MEDIA_STORAGE_BACKEND", default="filesystem") == "s3":
    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "access_key": env("MEDIA_S3_ACCESS_KEY_ID"),
            "secret_key": env("MEDIA_S3_SECRET_ACCESS_KEY"),
            "bucket_name": env("MEDIA_S3_PUBLIC_BUCKET"),
            "endpoint_url": env("MEDIA_S3_ENDPOINT_URL", default="https://storage.yandexcloud.net"),
            "region_name": env("MEDIA_S3_REGION", default="ru-central1"),
            "default_acl": "public-read",
            "querystring_auth": False,
            "file_overwrite": False,
        },
    }

SECRET_KEY = env("SECRET_KEY")
