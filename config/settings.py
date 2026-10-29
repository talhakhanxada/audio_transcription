import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.getenv("SECRET_KEY", "local-assignment-only-change-for-deployment")
DEBUG = os.getenv("DEBUG", "0") == "1"
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1],testserver").split(",")
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
INSTALLED_APPS = ["transcriptions"]
MIDDLEWARE = ["django.middleware.common.CommonMiddleware"]
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": DATA_DIR / "db.sqlite3",
    "OPTIONS": {"timeout": 30},
}}
MEDIA_ROOT = DATA_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
TRANSCRIPTION_BACKEND = os.getenv("TRANSCRIPTION_BACKEND", "whisper")
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "tiny")
CHUNK_SECONDS = int(os.getenv("CHUNK_SECONDS", "300"))
if CHUNK_SECONDS <= 0:
    raise ValueError("CHUNK_SECONDS must be positive")
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_AUDIO_SECONDS = 6 * 60 * 60
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
