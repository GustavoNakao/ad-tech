from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError


class RegrasPrecificacao(models.Model):
    """
    Parâmetros globais do motor de precificação R831 v2.
    Equivalente à tabela tb_parametros_globais do documento.
    Singleton — apenas 1 registro ativo por vez.
    """

    nome  = models.CharField(max_length=100, default="Configuração Principal")
    ativo = models.BooleanField(default=True)

    # ── Reputação da conta ────────────────────────────────────────────────────
    class ReputacaoConta(models.TextChoices):
        VERDE    = "verde",    "Verde / MercadoLíder (50% desconto frete)"
        OUTROS   = "outros",   "Outros (sem desconto frete)"

    reputacao_conta = models.CharField(
        max_length=10,
        choices=ReputacaoConta.choices,
        default=ReputacaoConta.VERDE,
        verbose_name="Reputação da Conta",
        help_text="Verde/MercadoLíder aplica 50% de desconto na tabela de frete do ML.",
    )

    # ── Denominador de blindagem ──────────────────────────────────────────────
    margem_minima = models.DecimalField(
        max_digits=5, decimal_places=4, default=0.1200,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        verbose_name="Margem Líquida Mínima",
        help_text="Piso de lucro líquido. Padrão: 12% (0.12).",
    )
    trava_fiscal = models.DecimalField(
        max_digits=5, decimal_places=4, default=0.0450,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        verbose_name="Trava Fiscal (TEF/DIFAL)",
        help_text="Reserva para obrigações fiscais estaduais. Padrão: 4,5% (0.045).",
    )

    # ── Comissões fallback (usadas quando category_id não está na tabela) ─────
    comissao_classico_fallback = models.DecimalField(
        max_digits=5, decimal_places=4, default=0.1150,
        verbose_name="Comissão Clássico (fallback)",
        help_text="Usado quando category_id não encontrado na tabela de comissões. Padrão: 11,5%.",
    )
    comissao_premium_fallback = models.DecimalField(
        max_digits=5, decimal_places=4, default=0.1650,
        verbose_name="Comissão Premium (fallback)",
        help_text="Usado quando category_id não encontrado na tabela de comissões. Padrão: 16,5%.",
    )

    # ── Tarifa Reduzida ───────────────────────────────────────────────────────
    comissao_classico_reduzida = models.DecimalField(
        max_digits=5, decimal_places=4, default=0.0950,
        verbose_name="Comissão Clássico Reduzida",
        help_text="Aplicada na faixa R$150–R$700 para categorias elegíveis. Padrão: 9,5%.",
    )
    comissao_premium_reduzida = models.DecimalField(
        max_digits=5, decimal_places=4, default=0.1400,
        verbose_name="Comissão Premium Reduzida",
        help_text="Aplicada na faixa R$150–R$700 para categorias elegíveis. Padrão: 14%.",
    )
    tarifa_reduzida_min = models.DecimalField(
        max_digits=8, decimal_places=2, default=150.01,
        verbose_name="Tarifa Reduzida — Mínimo (R$)",
        help_text="R$150,01 garante classificação inequívoca na faixa fiscal.",
    )
    tarifa_reduzida_max = models.DecimalField(
        max_digits=8, decimal_places=2, default=700.00,
        verbose_name="Tarifa Reduzida — Máximo (R$)",
    )

    # ── Custos operacionais fixos (numerador) ─────────────────────────────────
    custo_baselinker = models.DecimalField(
        max_digits=6, decimal_places=2, default=0.79,
        verbose_name="Custo BaseLinker (R$)",
        help_text="Taxa por anúncio integrado. Padrão: R$0,79.",
    )
    custo_gnre = models.DecimalField(
        max_digits=6, decimal_places=2, default=1.50,
        verbose_name="Custo GNRE/Operacional (R$)",
        help_text="Custo operacional por anúncio. Padrão: R$1,50.",
    )

    # ── Piso de frete grátis ──────────────────────────────────────────────────
    piso_frete_gratis = models.DecimalField(
        max_digits=8, decimal_places=2, default=79.00,
        verbose_name="Piso Frete Grátis ML (R$)",
        help_text="Abaixo deste valor, ML cobra taxa fixa por unidade.",
    )
    taxa_fixa_ml = models.DecimalField(
        max_digits=6, decimal_places=2, default=6.50,
        verbose_name="Taxa Fixa ML (R$)",
        help_text="Cobrada por unidade quando preço < piso_frete_gratis.",
    )

    # ── Faixa de risco (piso de cadastro) ────────────────────────────────────
    piso_faixa_min = models.DecimalField(
        max_digits=8, decimal_places=2, default=19.00,
        verbose_name="Faixa Risco — Início (R$)",
    )
    piso_faixa_max = models.DecimalField(
        max_digits=8, decimal_places=2, default=78.99,
        verbose_name="Faixa Risco — Fim (R$)",
    )
    piso_valor_forcado = models.DecimalField(
        max_digits=8, decimal_places=2, default=79.90,
        verbose_name="Valor Forçado fora da faixa (R$)",
    )

    # ── Preço de Vitrine ──────────────────────────────────────────────────────
    desconto_vitrine = models.DecimalField(
        max_digits=5, decimal_places=4, default=0.2000,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
        verbose_name="Desconto Vitrine (Central ML)",
        help_text="Percentual de desconto da Central de Promoções. Padrão: 20% (0.20).",
    )
    margem_maxima_vitrine = models.DecimalField(
        max_digits=5, decimal_places=4, default=0.6000,
        validators=[MinValueValidator(0), MaxValueValidator(5)],
        verbose_name="Margem Máxima Vitrine",
        help_text="Teto de markup sobre custo para evitar preços absurdos. Padrão: 60% (0.60).",
    )

    # ── Peso máximo para transportadora ──────────────────────────────────────
    peso_max_mercado_envios = models.DecimalField(
        max_digits=6, decimal_places=2, default=30.00,
        verbose_name="Peso Máximo Mercado Envios (kg)",
        help_text="Acima deste peso, usa custo_saida_transportadora obrigatoriamente.",
    )

    # ── Terminação de preço ───────────────────────────────────────────────────
    terminacao_preco = models.DecimalField(
        max_digits=4, decimal_places=2, default=0.90,
        verbose_name="Terminação de Preço",
        help_text="Ex: 0.90 → preços terminam em .90 (R$79,90).",
    )

    protocolo_greenfield = models.BooleanField(
        default=False,
        verbose_name="Protocolo Greenfield (v4.0)",
        help_text="Ativa a precificação simplificada com margem de 25% e sem custos operacionais fixos (BL/GNRE/Trava).",
    )

    # ── Auditoria ─────────────────────────────────────────────────────────────
    atualizado_em = models.DateTimeField(auto_now=True)
    criado_em     = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = "Regras de Precificação"
        verbose_name_plural = "Regras de Precificação"

    def __str__(self):
        status = "✅ ATIVA" if self.ativo else "⏸ Inativa"
        return f"{self.nome} [{status}]"

    def clean(self):
        divisor_minimo = (
            float(self.margem_minima) +
            float(self.trava_fiscal) +
            float(self.comissao_classico_fallback)
        )
        if divisor_minimo >= 1:
            raise ValidationError(
                f"Margem + Trava Fiscal + Comissão >= 100%. Divisor seria zero ou negativo."
            )

    @classmethod
    def get_ativa(cls) -> "RegrasPrecificacao":
        regra = cls.objects.filter(ativo=True).first()
        if not regra:
            regra = cls.objects.create(nome="Configuração Principal", ativo=True)
        return regra


class TabelaFreteMercadoEnvios(models.Model):
    """
    Tabela oficial do Mercado Envios por faixa de peso.
    O custo final aplica desconto de reputação da conta se configurado.
    """
    peso_min_kg = models.DecimalField(
        max_digits=8, decimal_places=3,
        verbose_name="Peso Mínimo (kg)",
    )
    peso_max_kg = models.DecimalField(
        max_digits=8, decimal_places=3,
        verbose_name="Peso Máximo (kg)",
        help_text="Usar 9999 para a última faixa (sem limite superior).",
    )
    custo_tabela = models.DecimalField(
        max_digits=8, decimal_places=2,
        verbose_name="Custo Tabela ML (R$)",
        help_text="Valor cheio antes do desconto de reputação.",
    )
    ativo = models.BooleanField(default=True)

    class Meta:
        verbose_name        = "Tabela Frete Mercado Envios"
        verbose_name_plural = "Tabela Frete Mercado Envios"
        ordering            = ["peso_min_kg"]

    def __str__(self):
        return f"{self.peso_min_kg}kg – {self.peso_max_kg}kg → R${self.custo_tabela}"


class CategoriaComissao(models.Model):
    """
    Tabela de comissões por category_id do ML.
    Equivalente à tabela tb_categorias_comissao do documento.
    """
    category_id = models.CharField(
        max_length=20, unique=True, db_index=True,
        verbose_name="Category ID (ML)",
        help_text="Ex: MLB1055",
    )
    nome_categoria = models.CharField(
        max_length=200, blank=True, default="",
        verbose_name="Nome da Categoria",
    )
    comissao_classico = models.DecimalField(
        max_digits=5, decimal_places=4,
        verbose_name="Comissão Clássico",
        help_text="Ex: 0.1150 = 11,5%",
    )
    comissao_premium = models.DecimalField(
        max_digits=5, decimal_places=4,
        verbose_name="Comissão Premium",
        help_text="Ex: 0.1600 = 16%",
    )
    elegivel_tarifa_reduzida = models.BooleanField(
        default=False,
        verbose_name="Elegível Tarifa Reduzida",
        help_text="Se True, aplica comissão reduzida na faixa R$150–R$700.",
    )
    ativo = models.BooleanField(default=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = "Comissão por Categoria ML"
        verbose_name_plural = "Comissões por Categoria ML"
        ordering            = ["nome_categoria"]

    def __str__(self):
        tar = " 🎯" if self.elegivel_tarifa_reduzida else ""
        return f"{self.category_id} — Clás:{float(self.comissao_classico):.1%} | Prem:{float(self.comissao_premium):.1%}{tar}"
