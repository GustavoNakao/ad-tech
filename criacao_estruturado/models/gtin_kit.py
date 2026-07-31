from django.db import models

class GtinKit(models.Model):
    """
    Persistência de GTINs (EANs) gerados para kits.
    Garante que o mesmo kit (Produto + Quantidade) sempre mantenha o mesmo GTIN,
    conforme documentação técnica (compartilhado entre Clássico e Premium).
    """
    sku_origem = models.CharField(max_length=60, db_index=True)
    sku_kit    = models.CharField(max_length=60, db_index=True, null=True, blank=True)
    quantidade = models.IntegerField()
    gtin       = models.CharField(max_length=20, db_index=True)
    criado_em  = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "GTIN de Kit"
        verbose_name_plural = "GTINs de Kits"
        constraints = [
            models.UniqueConstraint(
                fields=["sku_kit"],
                condition=models.Q(sku_kit__isnull=False),
                name="unique_gtin_por_sku_kit",
            )
        ]

    def __str__(self):
        return f"{self.sku_origem} (Kit {self.sku_kit} | Qtd {self.quantidade}) -> {self.gtin}"
