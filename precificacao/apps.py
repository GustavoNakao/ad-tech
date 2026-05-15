from django.apps import AppConfig


class PrecificacaoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name               = "precificacao"
    verbose_name       = "Precificação R831"

    def ready(self):
        from precificacao.signals import conectar_signals
        conectar_signals()
