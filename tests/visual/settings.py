"""Django settings for the local visual reference site only."""

from copy import deepcopy
from pathlib import Path

from config import settings as production_settings

for setting_name in dir(production_settings):
    if setting_name.isupper():
        globals()[setting_name] = getattr(production_settings, setting_name)

VISUAL_DIR = Path(__file__).resolve().parent

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "testserver"]
ROOT_URLCONF = "tests.visual.urls"
TEMPLATES = deepcopy(production_settings.TEMPLATES)
TEMPLATES[0]["DIRS"] = [VISUAL_DIR / "templates", *TEMPLATES[0]["DIRS"]]
STATICFILES_DIRS = [VISUAL_DIR / "static", *production_settings.STATICFILES_DIRS]
STORAGES = deepcopy(production_settings.STORAGES)
STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}
