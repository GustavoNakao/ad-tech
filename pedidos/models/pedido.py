from django.db import models


class Pedido(models.Model):
    """
    Representa um pedido importado do BaseLinker via getOrders.
    Campos escolhidos para cobrir operação, financeiro e entrega.
    """

    # ── Identificação ──────────────────────────────────────────────────────────
    id_baselinker     = models.BigIntegerField(unique=True, help_text="order_id da API")
    id_pedido_loja    = models.CharField(max_length=100, blank=True, help_text="shop_order_id")
    id_pedido_externo = models.CharField(max_length=100, blank=True, help_text="external_order_id")
    origem            = models.CharField(max_length=50,  blank=True, help_text="order_source (marketplace/loja)")
    id_origem         = models.CharField(max_length=50,  blank=True, help_text="order_source_id")

    # ── Status ─────────────────────────────────────────────────────────────────
    status    = models.ForeignKey(
        'StatusPedido',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='pedidos',
    )
    confirmado = models.BooleanField(default=False)

    # ── Datas ──────────────────────────────────────────────────────────────────
    data_criacao     = models.DateTimeField(null=True, blank=True, help_text="date_add")
    data_confirmacao = models.DateTimeField(null=True, blank=True, help_text="date_confirmed")
    data_no_status   = models.DateTimeField(null=True, blank=True, help_text="date_in_status")

    # ── Cliente ────────────────────────────────────────────────────────────────
    email    = models.EmailField(blank=True)
    telefone = models.CharField(max_length=30, blank=True)

    # ── Financeiro ─────────────────────────────────────────────────────────────
    moeda            = models.CharField(max_length=3, blank=True)
    valor_pago       = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    valor_frete      = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    metodo_pagamento = models.CharField(max_length=100, blank=True)

    # ── Entrega ────────────────────────────────────────────────────────────────
    metodo_entrega  = models.CharField(max_length=100, blank=True)
    numero_rastreio = models.CharField(max_length=100, blank=True)

    entrega_nome    = models.CharField(max_length=100, blank=True)
    entrega_endereco= models.CharField(max_length=255, blank=True)
    entrega_cep     = models.CharField(max_length=20,  blank=True)
    entrega_cidade  = models.CharField(max_length=100, blank=True)
    entrega_estado  = models.CharField(max_length=50,  blank=True)
    entrega_pais    = models.CharField(max_length=50,  blank=True)

    # ── Nota fiscal ────────────────────────────────────────────────────────────
    nf_nome     = models.CharField(max_length=200, blank=True)
    nf_cnpj_cpf = models.CharField(max_length=20,  blank=True)
    nf_endereco = models.CharField(max_length=255, blank=True)
    nf_cidade   = models.CharField(max_length=100, blank=True)
    nf_estado   = models.CharField(max_length=50,  blank=True)
    nf_cep      = models.CharField(max_length=20,  blank=True)

    # ── Comentários ────────────────────────────────────────────────────────────
    comentario_comprador = models.TextField(blank=True)
    comentario_vendedor  = models.TextField(blank=True)

    # ── Controle interno ───────────────────────────────────────────────────────
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        db_table     = 'pedido'
        verbose_name = 'Pedido'
        ordering     = ['-data_criacao']

    def __str__(self):
        return f'Pedido #{self.id_baselinker} — {self.origem}'
