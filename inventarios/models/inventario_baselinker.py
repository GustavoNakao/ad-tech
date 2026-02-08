# inventarios/models/inventario_baselinker.py


import uuid
from django.db import models
from django.utils import timezone


class InventarioBaselinker(models.Model):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text="ID interno do Django (UUID4)"
    )

    inventory_id = models.PositiveIntegerField(
        unique=True,
        db_index=True,
        help_text="ID único do inventário no BaseLinker"
    )

    nome = models.CharField(
        max_length=100,
        help_text="Nome do inventário (ex: 'Loja Brasil', 'Mercado Livre')"
    )

    descricao = models.TextField(
        blank=True,
        default='',
        help_text="Descrição do inventário (pode ser vazio)"
    )

    eh_padrao = models.BooleanField(
        default=False,
        help_text="Indica se este é o inventário padrão da conta"
    )

    armazens_vinculados = models.JSONField(
        default=list,
        blank=True,
        help_text="Lista de warehouse_id (ints) vinculados a este inventário"
    )

    idiomas = models.JSONField(
        default=list,
        blank=True,
        help_text="Lista de códigos de idioma suportados (ex: ['pt', 'en', 'es'])"
    )

    sincronizado_em = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Data/hora da última sincronização com a API"
    )

    criado_em = models.DateTimeField(
        default=timezone.now,
        editable=False,
    )

    class Meta:
        verbose_name = "Inventário BaseLinker"
        verbose_name_plural = "Inventários BaseLinker"
        ordering = ['-eh_padrao', 'nome']
        db_table = 'inventarios_inventario'
        indexes = [
            models.Index(fields=['inventory_id']),
            models.Index(fields=['eh_padrao']),
            models.Index(fields=['nome']),
        ]

    def __str__(self):
        padrao = " [PADRÃO]" if self.eh_padrao else ""
        return f"{self.nome}{padrao} (ID: {self.inventory_id})"

    def save(self, *args, **kwargs):
        if not isinstance(self.armazens_vinculados, list):
            self.armazens_vinculados = []
        if not isinstance(self.idiomas, list):
            self.idiomas = []
        super().save(*args, **kwargs)