from django.db import models

class StatusPedido(models.Model):
    id_baselinker = models.BigIntegerField(unique=True)
    nome = models.CharField(max_length=100)
    categoria_interna = models.CharField(max_length=50, blank=True)
    ignorar_atualizacao = models.BooleanField(default=False)

    class Meta:
        db_table = 'status_pedido'
        verbose_name = 'Status do Pedido'
        verbose_name_plural = 'Status dos Pedidos'

    def __str__(self):
        return f"[{self.id_baselinker}] {self.nome}"