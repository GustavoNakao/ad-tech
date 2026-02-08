# models/historico_kit.py
from django.db import models

class HistoricoKit(models.Model):
    sku_origem = models.CharField(max_length=60)
    id_origem = models.CharField(max_length=50)
    sku_kit = models.CharField(max_length=60, unique=True)
    id_kit = models.CharField(max_length=50)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Histórico de Kit"
        verbose_name_plural = "Históricos de Kits"
        # Permitimos múltiplos históricos para o mesmo produto de origem
        # (remoção da restrição unique_together para não bloquear recriações).

    def __str__(self):
        return f"Kit {self.sku_kit} ← {self.sku_origem}"