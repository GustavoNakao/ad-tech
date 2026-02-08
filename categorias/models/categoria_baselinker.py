# categorias/models/categoria_baselinker.py
import uuid
from django.db import models
from django.utils import timezone


class CategoriaBaselinker(models.Model):
    """
    Modelo PURAMENTE de dados para categorias do Configuracoes.
    ZERO dependência externa. 100% testável.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )

    category_id = models.PositiveIntegerField(
        unique=True,
        db_index=True,
        help_text="ID da categoria no Configuracoes"
    )

    name = models.CharField(max_length=200, db_index=True)

    # CORREÇÃO AQUI:
    # Removemos o campo manual 'parent_id' e deixamos só a ForeignKey.
    # O Django cria automaticamente o acesso ao ID via: objeto.parent_id
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children',
        # db_column='parent_id' # Não precisa forçar, o padrão do Django já é esse
    )

    # Removido: parent_id = models.PositiveIntegerField(...) -> CAUSAVA CONFLITO

    inventory_id = models.PositiveIntegerField(null=True, blank=True)
    ativo = models.BooleanField(default=True, db_index=True)

    sincronizado_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(default=timezone.now, editable=False)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Categoria"
        ordering = ['name']
        db_table = 'categorias'
        indexes = [
            # O Django exige que usemos 'parent' no índice, não 'parent_id' explicitamente
            models.Index(fields=['parent']),
            models.Index(fields=['inventory_id', 'ativo']),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.category_id})"