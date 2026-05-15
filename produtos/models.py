from django.db import models
from django.core.validators import MinValueValidator


class Produto(models.Model):
    """
    Representa um produto sincronizado com o BaseLinker.

    Separação de responsabilidades:
    - Dados de identificação/fiscais (SKU, NCM, Marca, Peso, Imagem) → vêm do BaseLinker via buscar_produto_por_sku()
    - Custo (custo_bruto_nf) → cadastrado manualmente pelo time ou importado do ERP (Bling/Tiny)
    - EAN → gerado exclusivamente pelo GeradorEANService via GS1, nunca editar manualmente
    - Precificação → PricingEngine usa custo_bruto_nf + dados físicos, não o raw da API
    """

    # ── Identificação ─────────────────────────────────────────────────────────
    sku = models.CharField(
        max_length=100,
        unique=True,
        db_index=True,
        verbose_name="SKU",
        help_text="Código interno. Chave de integração com o BaseLinker e o GeradorEANService.",
    )
    id_baselinker = models.CharField(
        max_length=50,
        unique=True,
        null=True,
        blank=True,
        verbose_name="ID BaseLinker",
        help_text="product_id retornado pela API do BaseLinker (getInventoryProductsList).",
    )
    nome = models.CharField(
        max_length=500,
        verbose_name="Nome do Produto",
    )

    # ── EAN — gerado exclusivamente pelo GeradorEANService ────────────────────
    # NÃO alterar este campo nem a lógica de geração.
    # O fluxo correto é: /gerador-ean/ → GeradorEANService → GS1 → salvar_ean_no_produto()
    ean = models.CharField(
        max_length=14,
        null=True,
        blank=True,
        db_index=True,
        verbose_name="EAN / GTIN",
        help_text="Gerado pelo GeradorEANService via GS1. Não editar manualmente.",
    )

    # ── Fiscal — obrigatórios para o GeradorEANService ────────────────────────
    # Estes campos são populados pelo BaseLinker via buscar_produto_por_sku().
    # O GeradorEANService valida ncm, marca, peso_kg e imagem_url antes de chamar a GS1.
    ncm = models.CharField(
        max_length=10,
        null=True,
        blank=True,
        verbose_name="NCM",
        help_text="Extraído do BaseLinker. Obrigatório para geração de EAN.",
    )
    marca = models.CharField(
        max_length=200,
        null=True,
        blank=True,
        verbose_name="Marca",
        help_text="Extraída do BaseLinker. Obrigatória para geração de EAN.",
    )
    codigo_origem = models.CharField(
        max_length=10,
        null=True,
        blank=True,
        default="076",
        verbose_name="Código de Origem",
        help_text="Código do país de origem. Padrão: 076 (Brasil).",
    )

    # ── Custo — cadastrado pelo time, não vem do BaseLinker ───────────────────
    custo_bruto_nf = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        verbose_name="Custo Bruto NF (R$)",
        help_text=(
            "Valor real pago ao fornecedor na NF. "
            "NÃO vem do BaseLinker — deve ser preenchido manualmente ou via ERP (Bling/Tiny). "
            "Base de todo o cálculo de preço pelo PricingEngine."
        ),
    )

    # ── Dados físicos — vêm do BaseLinker, usados pelo PricingEngine p/ frete ─
    peso_kg = models.DecimalField(
        max_digits=8,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        verbose_name="Peso Bruto (kg)",
        help_text="Extraído do BaseLinker. Obrigatório para EAN e estimativa de frete.",
    )
    altura_cm = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Altura (cm)",
        help_text="Extraído do BaseLinker. Usado para estimativa de frete por volume.",
    )
    largura_cm = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Largura (cm)",
    )
    comprimento_cm = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Comprimento (cm)",
    )

    # ── Imagem — extraída do BaseLinker, obrigatória para GS1 ─────────────────
    imagem_url = models.URLField(
        max_length=1000,
        null=True,
        blank=True,
        verbose_name="URL da Imagem Principal",
        help_text="Extraída do BaseLinker. Obrigatória para geração de EAN via GS1.",
    )

    # ── Concorrente — input manual do time de inteligência ───────────────────
    preco_concorrente = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Preço do Concorrente (R$)",
        help_text="Preço do líder de mercado. Usado para gerar o status verde/amarelo/vermelho.",
    )
    link_concorrente = models.URLField(
        max_length=1000,
        null=True,
        blank=True,
        verbose_name="Link do Concorrente",
        help_text="URL do anúncio do concorrente líder para referência do time.",
    )

    # ── Status de venda ───────────────────────────────────────────────────────
    class StatusVenda(models.TextChoices):
        LANCAMENTO = "lancamento", "🚀 Lançamento"
        ATIVO      = "ativo",      "✅ Ativo"
        PAUSADO    = "pausado",    "⏸ Pausado"
        INATIVO    = "inativo",    "❌ Inativo"

    status_venda = models.CharField(
        max_length=20,
        choices=StatusVenda.choices,
        default=StatusVenda.LANCAMENTO,
        verbose_name="Status de Venda",
    )

    # ── Auditoria ─────────────────────────────────────────────────────────────
    importado_em  = models.DateTimeField(auto_now_add=True, verbose_name="Importado em")
    atualizado_em = models.DateTimeField(auto_now=True,     verbose_name="Atualizado em")

    class Meta:
        verbose_name        = "Produto"
        verbose_name_plural = "Produtos"
        ordering            = ["-importado_em"]

    def __str__(self):
        return f"[{self.sku}] {self.nome}"

    # ── Properties utilitárias ────────────────────────────────────────────────

    @property
    def tem_ean(self) -> bool:
        """True se o EAN já foi gerado pelo GeradorEANService."""
        return bool(self.ean)

    @property
    def tem_dados_fiscais(self) -> bool:
        """
        True se o produto tem todos os dados exigidos pelo GeradorEANService:
        NCM, Marca (não vazia), Peso > 0 e Imagem.
        Espelha exatamente as validações do gerador_ean.py.
        """
        return bool(
            self.ncm
            and self.marca
            and self.marca.strip() not in ("", "Marca Não Informada")
            and self.peso_kg
            and self.peso_kg > 0
            and self.imagem_url
        )

    @property
    def pronto_para_ean(self) -> bool:
        """True se pode ir para o GeradorEANService: tem dados fiscais e ainda não tem EAN."""
        return self.tem_dados_fiscais and not self.tem_ean

    @property
    def tem_custo(self) -> bool:
        """True se o custo foi preenchido pelo time (pré-requisito para precificação)."""
        return bool(self.custo_bruto_nf and self.custo_bruto_nf > 0)

    @property
    def dados_para_precificacao(self) -> dict:
        """
        Monta o dict que o PricingEngine espera.
        Usa apenas campos do model — custo vem de custo_bruto_nf (não do BaseLinker).
        O PricingEngine usa 'average_cost' como campo de custo principal.
        """
        return {
            "average_cost": float(self.custo_bruto_nf) if self.custo_bruto_nf else 0.0,
            "weight":       float(self.peso_kg)        if self.peso_kg        else None,
            "height":       float(self.altura_cm)      if self.altura_cm      else None,
            "width":        float(self.largura_cm)      if self.largura_cm     else None,
            "length":       float(self.comprimento_cm) if self.comprimento_cm else None,
        }
