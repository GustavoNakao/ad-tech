from django.db import models

class ItemPedido(models.Model):
    # TRUQUE PARA EVITAR ERRO: Usando 'Pedido' como String
    pedido                = models.ForeignKey('Pedido', on_delete=models.CASCADE, related_name='itens')
    
    id_item_baselinker    = models.BigIntegerField(unique=True) 
    id_produto_baselinker = models.CharField(max_length=50, blank=True)
    id_variante           = models.CharField(max_length=30, blank=True)
    tipo_estoque          = models.CharField(max_length=9, blank=True)
    id_estoque            = models.IntegerField(null=True, blank=True)
    id_deposito           = models.IntegerField(null=True, blank=True)
    nome                  = models.CharField(max_length=255)
    sku                   = models.CharField(max_length=50, blank=True)
    ean                   = models.CharField(max_length=32, blank=True)
    atributos             = models.CharField(max_length=350, blank=True)
    localizacao           = models.CharField(max_length=50, blank=True)
    id_anuncio            = models.CharField(max_length=50, blank=True)
    preco_unitario        = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    preco_custo           = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True) 
    taxa                  = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    quantidade            = models.IntegerField(default=1)
    peso                  = models.DecimalField(max_digits=8, decimal_places=3, default=0)
    id_bundle             = models.IntegerField(default=0)

    class Meta:
        db_table = 'item_pedido'
        verbose_name = 'Item do Pedido'

    def __str__(self):
        return f'{self.quantidade}x {self.sku} (Pedido #{self.pedido.id_baselinker})'