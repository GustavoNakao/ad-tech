# models/baselinker_kit_sku.py
from django.db import models
from django.utils import timezone


class BaselinkerKitSku(models.Model):
    """
    Cache/local dos SKUs existentes na categoria de kits do BaseLinker.
    Usado para reconciliação e identificação de SKUs ausentes (→ recycled).
    """
    sku = models.PositiveIntegerField(
        unique=True,
        db_index=True,
        help_text="SKU do kit no BaseLinker"
    )
    product_id = models.CharField(
        max_length=50,
        blank=True,
        default="",
        help_text="ID do produto no BaseLinker (quando disponível)"
    )
    sincronizado_em = models.DateTimeField(
        default=timezone.now,
        help_text="Quando este SKU foi visto pela última vez no BaseLinker"
    )

    class Meta:
        verbose_name = "SKU Kit BaseLinker"
        verbose_name_plural = "SKUs Kits BaseLinker"
        db_table = "criacao_estruturado_baselinkerkitsku"
        ordering = ["sku"]

    def __str__(self):
        return f"SKU {self.sku}"
