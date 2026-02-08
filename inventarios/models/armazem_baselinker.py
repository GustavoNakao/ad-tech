# inventarios/models/armazem_baselinker.py
import uuid
from django.db import models
from django.utils import timezone


class ArmazemBaselinker(models.Model):
    """
    Modelo para armazéns (warehouses) do BaseLinker.
    Baseado exatamente na API getInventoryWarehouses:
    - warehouse_id: int (único)
    - name: str (varchar(100))
    - warehouse_type: str (varchar(30) - 'bl', 'shop', 'warehouse')
    - description: str (text) - pode ser vazio
    - stock_edition: bool
    - is_default: bool
    """

    # PK interna segura (UUID) - evita conflito com warehouse_id
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text="ID interno do Django (UUID4)"
    )

    warehouse_id = models.PositiveIntegerField(
        unique=True,
        db_index=True,
        help_text="ID único do armazém no BaseLinker (int)"
    )

    nome = models.CharField(
        max_length=100,
        help_text="Nome do armazém (varchar(100))"
    )

    tipo = models.CharField(
        max_length=30,
        help_text="Tipo do armazém ('bl', 'shop', 'warehouse')"
    )

    descricao = models.TextField(
        blank=True,  # ← CORREÇÃO: permite vazio da API
        default='',
        help_text="Descrição do armazém (text - pode ser vazio)"
    )

    edicao_estoque = models.BooleanField(
        default=False,
        help_text="Permite edição manual de estoque (bool)"
    )

    eh_padrao = models.BooleanField(
        default=False,
        help_text="É o armazém padrão (bool)"
    )

    sincronizado_em = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Data da última sincronização"
    )

    criado_em = models.DateTimeField(
        default=timezone.now,
        editable=False,
        help_text="Data de criação"
    )

    class Meta:
        verbose_name = "Armazém BaseLinker"
        verbose_name_plural = "Armazéns BaseLinker"
        ordering = ['nome']
        db_table = 'inventarios_armazem'
        indexes = [
            models.Index(fields=['tipo', 'eh_padrao']),
            models.Index(fields=['warehouse_id']),
        ]

    def __str__(self):
        return f"{self.nome} (ID: {self.warehouse_id}, Tipo: {self.tipo})"