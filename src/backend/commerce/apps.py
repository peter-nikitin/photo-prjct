from django.apps import AppConfig


class CommerceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "commerce"
    verbose_name = "Commerce"

    def ready(self) -> None:
        import commerce.checks  # noqa: F401
