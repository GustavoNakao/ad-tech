from django.db import models
from django.contrib.auth.models import User


class HistoricoIA(models.Model):
    """
    Registra cada execução do pipeline de criação via IA.

    Separado do HistoricoKit (kits manuais) — responsabilidades distintas:
        HistoricoKit  → kits criados manualmente pelo time via painel
        HistoricoIA   → produtos criados pelo pipeline automatizado (URL → IA → BaseLinker)

    O par payload_ia_cru / payload_pos_revisao é a chave para refinar o prompt
    com o tempo — diferença entre os dois revela onde a IA erra.
    """

    class Status(models.TextChoices):
        PENDENTE     = "PENDENTE",     "⏳ Pendente"
        QUARENTENA   = "QUARENTENA",   "🚫 Quarentena (termo proibido)"
        REVISAO      = "REVISAO",      "👁 Aguardando Revisão"
        FALHA_IA     = "FALHA_IA",     "❌ Falha na IA"
        FALHA_ML     = "FALHA_ML",     "❌ Falha na API ML"
        FALHA_BL     = "FALHA_BL",     "❌ Falha no BaseLinker"
        CONCLUIDO    = "CONCLUIDO",    "✅ Concluído"

    # ── Entrada ───────────────────────────────────────────────────────────────
    url_original = models.URLField(
        max_length=1000,
        verbose_name="URL do Concorrente",
        help_text="URL do anúncio concorrente inserida pelo time.",
    )
    item_id_ml = models.CharField(
        max_length=20,
        blank=True,
        default="",
        verbose_name="Item ID (ML)",
        help_text="ID extraído da URL. Ex: MLB123456789",
    )
    titulo_original = models.CharField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="Título Original (ML)",
        help_text="Título do anúncio concorrente coletado da API ML.",
    )
    category_id_ml = models.CharField(
        max_length=20,
        blank=True,
        default="",
        verbose_name="Category ID Folha (ML)",
        help_text="Categoria folha do produto concorrente. Ex: MLB178600",
    )
    domain_id_ml = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="Domain ID (ML)",
        help_text="Domínio raiz. Ex: MLB-BEAUTY",
        db_index=True,
    )

    # ── Compliance ────────────────────────────────────────────────────────────
    termo_bloqueado = models.CharField(
        max_length=500,
        blank=True,
        default="",
        verbose_name="Termo Bloqueado",
        help_text="Regex que bloqueou o produto. Preenchido só se foi para Quarentena.",
    )

    # ── Resultado da IA ───────────────────────────────────────────────────────
    payload_ia_cru = models.JSONField(
        null=True,
        blank=True,
        verbose_name="Payload IA (Bruto)",
        help_text=(
            "Resposta bruta da IA antes de qualquer toque humano. "
            "Preservado para análise e refinamento do prompt."
        ),
    )
    payload_pos_revisao = models.JSONField(
        null=True,
        blank=True,
        verbose_name="Payload Pós-Revisão",
        help_text=(
            "JSON final após edição humana. "
            "A diferença entre este e payload_ia_cru mostra onde a IA errou."
        ),
    )
    violacao_politica_ml = models.BooleanField(
        default=False,
        verbose_name="Violação de Política ML",
        help_text="Flag retornada pela IA: pirataria, item médico sem registro, arma disfarçada, etc.",
    )

    # ── Score determinístico ──────────────────────────────────────────────────
    score_ia = models.DecimalField(
        max_digits=4,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name="Score IA",
        help_text=(
            "Score calculado pelo backend: atributos_corretos / total_atributos_obrigatorios. "
            "A IA não opina sobre sua própria confiança — o backend calcula."
        ),
    )
    atributos_total     = models.IntegerField(default=0, verbose_name="Total de Atributos Obrigatórios")
    atributos_corretos  = models.IntegerField(default=0, verbose_name="Atributos Preenchidos Corretamente")

    # ── Destino no BaseLinker ─────────────────────────────────────────────────
    category_id_baselinker = models.IntegerField(
        null=True,
        blank=True,
        verbose_name="Category ID (BaseLinker)",
    )
    sku_gerado = models.CharField(
        max_length=100,
        blank=True,
        default="",
        verbose_name="SKU Gerado",
        help_text="SKU criado no BaseLinker após conclusão.",
    )
    product_id_baselinker = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name="Product ID (BaseLinker)",
        help_text="ID do produto criado no BaseLinker.",
    )
    foi_para_rascunho = models.BooleanField(
        default=False,
        verbose_name="Foi para Rascunho",
        help_text="True se o produto foi para o warehouse 'Rascunhos IA' aguardando revisão.",
    )
    motivo_rascunho = models.CharField(
        max_length=50,
        blank=True,
        default="",
        verbose_name="Motivo do Rascunho",
        help_text="Ex: score_baixo, revisao_manual, dominio_nao_mapeado, violacao_politica",
    )

    # ── Revisão humana ────────────────────────────────────────────────────────
    revisado_por = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        verbose_name="Revisado por",
        help_text="Nulo se passou sem revisão humana.",
        related_name="revisoes_ia",
    )
    revisado_em = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="Revisado em",
    )

    # ── Status e auditoria ────────────────────────────────────────────────────
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDENTE,
        verbose_name="Status",
        db_index=True,
    )
    erro_detalhe = models.TextField(
        blank=True,
        default="",
        verbose_name="Detalhe do Erro",
        help_text="Mensagem técnica de erro para debug. Visível só para gestores.",
    )
    data_processamento = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Data de Processamento",
    )
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = "Histórico IA"
        verbose_name_plural = "Histórico IA"
        ordering            = ["-data_processamento"]

    def __str__(self):
        return f"[{self.status}] {self.item_id_ml or self.url_original[:50]}"

    @property
    def score_formatado(self) -> str:
        if self.score_ia is None:
            return "—"
        return f"{float(self.score_ia) * 100:.0f}%"

    @property
    def precisa_revisao(self) -> bool:
        return self.status == self.Status.REVISAO
