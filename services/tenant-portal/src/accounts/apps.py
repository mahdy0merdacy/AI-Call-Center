from django.apps import AppConfig


class AccountsConfig(AppConfig):
    name = "accounts"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self) -> None:
        # Registers the drf-spectacular extension (Swagger "Authorize" button).
        from accounts import schema  # noqa: F401
