from django.apps import AppConfig


class ProcessingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "processing"

    def ready(self) -> None:
        import processing.checks  # noqa: F401
        import processing.signals  # noqa: F401
