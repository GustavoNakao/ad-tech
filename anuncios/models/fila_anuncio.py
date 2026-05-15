from django.db import models
from django.core.validators import MinValueValidator


class FilaAnuncio(models.Model):
    """
    Fila de trabalho para criação de anúncios no Mercado Livre via BaseLinker.

    Fluxo:
        1. Time de Inteligência insere o item (produto + concorrente).
        2. PricingEngine calcula o preço e preenche os campos de resultado.
        3. Time de Cadastro consulta o status (verde/amarelo/vermelho) e age.
        4. Após criar no ML, o status muda para PUBLICADO.
    """

    # ── Tipo de anúncio ───────────────────────────────────────────────────────
    class TipoAnuncio(models.TextChoices):
        UNIT  = "UNIT",  "Unitário — Isca de conveniência"
        MULTI = "MULTI", "Multi — Kit do mesmo produto (x2, x3…)"
        MISTO = "MISTO", "Misto — Kit com produtos diferentes (cross-sell)"

    tipo = models.CharField(
        max_length=5,
        choices=TipoAnuncio.choices,
        default=TipoAnuncio.UNIT,
        verbose_name="Tipo de Anúncio",
    )

    # ── Produto principal ─────────────────────────────────────────────────────
    sku_principal = models.CharField(
        max_length=100,
        verbose_name="SKU Principal",
        help_text="SKU do produto principal (ou único, para UNIT/MULTI).",
        db_index=True,
    )
    qtd_principal = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        verbose_name="Qtd. Principal",
        help_text="Quantidade do produto principal no kit.",
    )

    # ── Produto secundário (apenas MISTO) ─────────────────────────────────────
    sku_secundario = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name="SKU Secundário",
        help_text="Apenas para anúncios MISTO. SKU do segundo produto do kit.",
    )
    qtd_secundario = models.PositiveIntegerField(
        default=0,
        verbose_name="Qtd. Secundária",
        help_text="Quantidade do produto secundário no kit.",
    )

    # ── URL do produto (para referência e scraping) ───────────────────────────
    url_produto = models.URLField(
        max_length=2000,
        null=True,
        blank=True,
        verbose_name="URL do Produto",
        help_text=(
            "URL do produto (catálogo interno, fabricante ou referência). "
            "Usada para extrair informações adicionais e auxiliar no cadastro manual."
        ),
    )

    # ── Concorrente ───────────────────────────────────────────────────────────
    link_concorrente  = models.URLField(max_length=1000, null=True, blank=True, verbose_name="Link do Concorrente")
    preco_concorrente = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        verbose_name="Preço do Concorrente (R$)",
        help_text="Preço do líder de mercado. Obrigatório para análise verde/amarelo/vermelho.",
    )

    # ── Resultado calculado pelo PricingEngine ────────────────────────────────
    preco_sugerido = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        verbose_name="Preço Sugerido R831 (R$)",
        help_text="Preenchido automaticamente pelo PricingEngine.",
    )
    margem_real = models.DecimalField(
        max_digits=5, decimal_places=4, null=True, blank=True,
        verbose_name="Margem Real",
        help_text="Margem calculada comparando custo vs preço do concorrente.",
    )
    estrategia_gerada = models.TextField(
        null=True, blank=True,
        verbose_name="Estratégia Gerada",
        help_text="Texto gerado pelo engine: bater preço, anti-anúncio ou inviável.",
    )

    # ── Status de margem (verde / amarelo / vermelho) ─────────────────────────
    class StatusMargem(models.TextChoices):
        PENDENTE  = "pendente",  "⏳ Pendente — aguardando cálculo"
        VERDE     = "verde",     "🟢 Verde — pode cadastrar"
        AMARELO   = "amarelo",   "🟡 Amarelo — atenção, margem apertada"
        VERMELHO  = "vermelho",  "🔴 Vermelho — não cadastrar"

    status_margem = models.CharField(
        max_length=10,
        choices=StatusMargem.choices,
        default=StatusMargem.PENDENTE,
        verbose_name="Status de Margem",
        db_index=True,
    )

    # ── Status de publicação ──────────────────────────────────────────────────
    class StatusPublicacao(models.TextChoices):
        FILA       = "fila",       "📋 Na Fila"
        PUBLICADO  = "publicado",  "✅ Publicado"
        BLOQUEADO  = "bloqueado",  "🔴 Bloqueado"
        REVISAO    = "revisao",    "🔄 Em Revisão"

    status_publicacao = models.CharField(
        max_length=10,
        choices=StatusPublicacao.choices,
        default=StatusPublicacao.FILA,
        verbose_name="Status de Publicação",
        db_index=True,
    )

    # ── Observações ───────────────────────────────────────────────────────────
    observacao = models.TextField(
        null=True, blank=True,
        verbose_name="Observação",
        help_text="Notas internas do time de cadastro.",
    )

    # ── Auditoria ─────────────────────────────────────────────────────────────
    criado_em      = models.DateTimeField(auto_now_add=True, verbose_name="Criado em")
    atualizado_em  = models.DateTimeField(auto_now=True,     verbose_name="Atualizado em")
    processado_em  = models.DateTimeField(
        null=True, blank=True,
        verbose_name="Processado em",
        help_text="Quando o PricingEngine calculou o preço.",
    )

    class Meta:
        verbose_name        = "Fila de Anúncio"
        verbose_name_plural = "Fila de Anúncios"
        ordering            = ["-criado_em"]

    def __str__(self):
        secundario = f" + {self.sku_secundario}" if self.sku_secundario else ""
        return (
            f"[{self.tipo}] {self.sku_principal}{secundario} "
            f"| {self.get_status_margem_display()}"
        )

    @property
    def e_kit(self) -> bool:
        return self.tipo in (self.TipoAnuncio.MULTI, self.TipoAnuncio.MISTO)

    @property
    def descricao_kit(self) -> str:
        """Descrição legível da composição do kit."""
        if self.tipo == self.TipoAnuncio.UNIT:
            return f"1x {self.sku_principal}"
        if self.tipo == self.TipoAnuncio.MULTI:
            return f"{self.qtd_principal}x {self.sku_principal}"
        # MISTO
        sec = f" + {self.qtd_secundario}x {self.sku_secundario}" if self.sku_secundario else ""
        return f"{self.qtd_principal}x {self.sku_principal}{sec}"
