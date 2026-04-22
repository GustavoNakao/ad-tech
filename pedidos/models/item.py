from django.db import models


class ItemPedido(models.Model):
    """
    Representa um produto dentro de um pedido BaseLinker.
    Chave natural: id_item_baselinker (order_product_id).
    """

    pedido = models.ForeignKey(
        'Pedido',
        on_delete=models.CASCADE,
        related_name='itens',
    )

    # ── Identificação do item ──────────────────────────────────────────────────
    id_item_baselinker    = models.BigIntegerField(unique=True, help_text="order_product_id")
    id_produto_baselinker = models.CharField(max_length=50, blank=True, help_text="product_id")
    id_variante           = models.CharField(max_length=50, blank=True, help_text="variant_id")

    # ── Dados do produto ───────────────────────────────────────────────────────
    nome       = models.CharField(max_length=255)
    sku        = models.CharField(max_length=100, blank=True)
    ean        = models.CharField(max_length=20,  blank=True)
    atributos  = models.CharField(max_length=500, blank=True, help_text="Cor, tamanho, etc.")

    # ── Valores ────────────────────────────────────────────────────────────────
    quantidade     = models.IntegerField(default=1)
    preco_unitario = models.DecimalField(max_digits=12, decimal_places=2, default=0, help_text="price_brutto")
    taxa           = models.DecimalField(max_digits=6,  decimal_places=2, default=0, help_text="% de imposto (tax_rate)")
    peso           = models.DecimalField(max_digits=8,  decimal_places=3, default=0, help_text="kg")

    class Meta:
        db_table     = 'item_pedido'
        verbose_name = 'Item do Pedido'

    def __str__(self):
        return f'{self.quantidade}x {self.sku or self.nome} (Pedido #{self.pedido.id_baselinker})'
