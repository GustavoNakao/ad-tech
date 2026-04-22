from django.db import models


class StatusPedido(models.Model):
    """
    Representa um status de pedido vindo do BaseLinker.
    Populado automaticamente durante a sincronização.
    """
    id_baselinker       = models.IntegerField(unique=True)
    nome                = models.CharField(max_length=100)
    # Marque True para que pedidos nesse status não sejam sobrescritos na sync
    ignorar_atualizacao = models.BooleanField(default=False)

    class Meta:
        db_table     = 'status_pedido'
        verbose_name = 'Status do Pedido'
        verbose_name_plural = 'Status dos Pedidos'
        ordering = ['id_baselinker']

    def __str__(self):
        return f'[{self.id_baselinker}] {self.nome}'
