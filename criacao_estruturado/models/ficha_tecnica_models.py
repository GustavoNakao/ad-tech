"""
criacao_estruturado/models/ficha_tecnica_models.py

Modelos Django para persistência e auditoria de Fichas Técnicas no PostgreSQL.
Registra o histórico auditável de cada execução: por que cada atributo foi enviado, alterado ou bloqueado.
"""
from django.db import models


class FichaExecucao(models.Model):
    """
    Registro histórico de cada execução do pipeline de Ficha Técnica.
    """
    sku = models.CharField(max_length=60, db_index=True)
    id_kit = models.CharField(max_length=50, blank=True, default="")
    category_id = models.CharField(max_length=50, db_index=True, default="", blank=True)
    modelo_extrator = models.CharField(max_length=50, default="groq/gpt-oss-120b")
    modelo_auditor = models.CharField(max_length=50, default="gemini-2.5-flash")
    status = models.CharField(max_length=20, choices=[("APROVADO", "Aprovado"), ("BLOQUEADO", "Bloqueado"), ("REVISAR", "Revisar")], default="APROVADO")
    score_confiabilidade = models.FloatField(default=100.0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Ficha Técnica — Execução"
        verbose_name_plural = "Ficha Técnica — Execuções"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Execução {self.sku} ({self.status})"


class FichaAtributo(models.Model):
    """
    Registro individual de cada atributo processado na execução.
    """
    execucao = models.ForeignKey(FichaExecucao, on_delete=models.CASCADE, related_name="atributos")
    nome = models.CharField(max_length=100)
    valor_original = models.TextField(blank=True, null=True)
    valor_normalizado = models.TextField(blank=True, null=True)
    status = models.CharField(max_length=30)  # CONFIRMADO, CONTRADITO, NAO_VERIFICADO, etc.
    fonte_principal = models.CharField(max_length=50)

    class Meta:
        verbose_name = "Ficha Técnica — Atributo"
        verbose_name_plural = "Ficha Técnica — Atributos"

    def __str__(self) -> str:
        return f"{self.nome}: {self.valor_normalizado} ({self.status})"


class FichaEvidencia(models.Model):
    """
    Evidência factual coletada para um atributo específico.
    """
    atributo = models.ForeignKey(FichaAtributo, on_delete=models.CASCADE, related_name="evidencias")
    fonte = models.CharField(max_length=50)
    valor = models.TextField()
    trecho = models.TextField(blank=True, default="")
    prioridade = models.IntegerField(default=50)

    class Meta:
        verbose_name = "Ficha Técnica — Evidência"
        verbose_name_plural = "Ficha Técnica — Evidências"


class FichaAuditoria(models.Model):
    """
    Resultado da auditoria individual realizada pelo Agente 7 (Gemini).
    """
    atributo = models.ForeignKey(FichaAtributo, on_delete=models.CASCADE, related_name="auditorias")
    modelo = models.CharField(max_length=50, default="gemini-2.5-flash")
    status_auditoria = models.CharField(max_length=30)
    valor_analisado = models.TextField(blank=True, null=True)
    valor_recomendado = models.TextField(blank=True, null=True)
    justificativa = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Ficha Técnica — Auditoria"
        verbose_name_plural = "Ficha Técnica — Auditorias"


class FichaConflito(models.Model):
    """
    Registro de conflitos entre fontes detectados e resolvidos pelo sistema.
    """
    execucao = models.ForeignKey(FichaExecucao, on_delete=models.CASCADE, related_name="conflitos")
    atributo_nome = models.CharField(max_length=100)
    fonte_a = models.CharField(max_length=50)
    valor_a = models.TextField()
    fonte_b = models.CharField(max_length=50)
    valor_b = models.TextField()
    resolucao = models.TextField()

    class Meta:
        verbose_name = "Ficha Técnica — Conflito"
        verbose_name_plural = "Ficha Técnica — Conflitos"
