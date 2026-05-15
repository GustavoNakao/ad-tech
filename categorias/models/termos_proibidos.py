import re
from django.db import models


class TermosProibidos(models.Model):
    """
    Tabela de termos proibidos com regex.
    Aplicada antes de qualquer chamada à IA — zero custo de API quando há match.

    Exemplos de padrões:
        \\barma(s)?\\b         → arma, armas
        \\bfake\\b             → fake (word boundary)
        \\bréplica\\b          → réplica
        \\bsem registro\\b     → medicamento sem registro
    """

    padrao_regex = models.CharField(
        max_length=500,
        verbose_name="Padrão Regex",
        help_text=(
            "Expressão regular com word boundary. Ex: \\\\barma(s)?\\\\b. "
            "Use re.IGNORECASE — o sistema já aplica flag de case insensitive."
        ),
    )
    descricao = models.CharField(
        max_length=300,
        blank=True,
        default="",
        verbose_name="Descrição",
        help_text="Explica por que esse termo é proibido. Ex: 'Armas de fogo — proibido pelo ML'",
    )

    class Categoria(models.TextChoices):
        PIRATARIA    = "pirataria",    "🏴‍☠️ Pirataria / Réplica"
        ARMAS        = "armas",        "🔫 Armas e Acessórios"
        MEDICAMENTO  = "medicamento",  "💊 Medicamento sem Registro"
        ADULTO       = "adulto",       "🔞 Conteúdo Adulto"
        FINANCEIRO   = "financeiro",   "💰 Financeiro / Sorteios"
        SENSIVEL     = "sensivel",     "🧠 Sensível / Ofensivo"
        PATRIMONIO   = "patrimonio",   "🏛️ Patrimônio / Ingressos"
        DIGITAL      = "digital",      "💻 Software / Contas Digitais"
        NAO_PRODUTO  = "nao_produto",  "🚫 Não é um Produto (Busca/Troca)"
        OUTROS       = "outros",       "⚠️ Outros"

    categoria = models.CharField(
        max_length=20,
        choices=Categoria.choices,
        default=Categoria.OUTROS,
        verbose_name="Categoria",
        db_index=True,
    )
    ativo = models.BooleanField(
        default=True,
        verbose_name="Ativo",
        db_index=True,
        help_text="Desativar a regra sem deletar.",
    )
    criado_em     = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = "Termo Proibido"
        verbose_name_plural = "Termos Proibidos"
        ordering            = ["categoria", "padrao_regex"]

    def __str__(self):
        status = "✅" if self.ativo else "❌"
        return f"{status} [{self.get_categoria_display()}] {self.padrao_regex}"

    # ── Métodos de classe ─────────────────────────────────────────────────────

    @classmethod
    def verificar(cls, texto: str) -> dict:
        """
        Verifica se o texto bate com algum termo proibido ativo.
        Chamado no Passo 2 do pipeline antes de qualquer chamada à IA.

        Retorna:
            {
                "bloqueado":  True/False,
                "termo":      "\\\\barma\\\\b" ou None,
                "categoria":  "armas" ou None,
                "descricao":  "Armas de fogo..." ou None,
            }
        """
        termos_ativos = cls.objects.filter(ativo=True).values(
            "padrao_regex", "categoria", "descricao"
        )

        for termo in termos_ativos:
            try:
                if re.search(termo["padrao_regex"], texto, re.IGNORECASE):
                    return {
                        "bloqueado":  True,
                        "termo":      termo["padrao_regex"],
                        "categoria":  termo["categoria"],
                        "descricao":  termo["descricao"],
                    }
            except re.error:
                continue  # regex inválida — ignora silenciosamente

        return {
            "bloqueado":  False,
            "termo":      None,
            "categoria":  None,
            "descricao":  None,
        }
