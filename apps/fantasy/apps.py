from django.apps import AppConfig


class FantasyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.fantasy"
    verbose_name = "Fantasy"

    def ready(self):
        from . import signals  # noqa: F401 -- connects the receivers
