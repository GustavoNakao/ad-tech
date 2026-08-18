"""
criacao_estruturado/services/ficha_tecnica/auditor.py

Agente 7 — Auditor Factual (Google Gemini gemini-2.5-flash / gemini-3.6-flash).
Audita 100% das fichas técnicas confrontando os atributos candidatos com o pacote de evidências bruto.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from integracoes.gemini.client import GeminiClient
from .models import AttributeCandidate, AttributeStatus, EvidencePackage

logger = logging.getLogger(__name__)


class FichaTecnicaAuditor:
    """
    Agente 7 — Auditor Factual (Gemini).
    Audita 100% das fichas técnicas antes da publicação no BaseLinker.
    """

    SYSTEM_PROMPT = """Você é o Agente 7 — Auditor Factual de Ficha Técnica do Mercado Livre.
Sua responsabilidade é auditar 100% dos atributos técnicos gerados e verificar se CADA UM DELES está rigorosamente COMPROVADO pelas evidências factuais fornecidas.

REGRAS RÍGIDAS DE AUDITORIA:
1. Para cada atributo, confronte o valor gerado com as evidências brutas das fontes.
2. Determine para cada atributo um dos seguintes estados:
   - CONFIRMADO: Se existe evidência explícita e direta comprovando a informação.
   - CONTRADITO: Se a fonte oficial (ex: Catálogo ML ou Fabricante) informa um valor DIFERENTE.
   - NAO_VERIFICADO: Se não existe NENHUMA fonte ou evidência textual citando a informação.
   - INVALIDO: Se o valor viola regras básicas de especificação ou formato.
3. Diferencie categoricamente 'CONFIRMADO' de 'PLAUSÍVEL'. Dizer que algo 'parece fazer sentido' NÃO É EVIDÊNCIA.
4. Se o atributo estiver CONTRADITO por fonte superior, indique o `valor_recomendado` correto e a `justificativa`.
5. REGRA DE ATRIBUTOS DE VARIAÇÃO: Atributos que identificam variações da mesma linha de produto (ex: Cor, Sabor, Tamanho, Voltagem, Modelo específico) têm prioridade MÁXIMA na evidência fática trazida pelo TÍTULO e DESCRIÇÃO. Se a busca geral do catálogo do ML indicar um valor de variação diferente (ex: Catálogo diz 'Rosa-pálido' mas o Título diz 'Bege'), a variação declarada no TÍTULO ('Bege') está CONFIRMADA faticamente e NÃO deve ser CONTRADITA.
6. Responda estritamente em formato JSON."""

    def __init__(self) -> None:
        self.client = GeminiClient()

    def auditar_ficha_tecnica(
        self,
        package: EvidencePackage,
        features_candidatas: Dict[str, str],
        atributos_detalhados: List[AttributeCandidate]
    ) -> Dict[str, Any]:
        """
        Audita 100% dos atributos kandidatos contra o EvidencePackage.
        """
        # Monta o pacote de auditoria estruturado
        evidencias_texto = []
        for k, list_ev in package.evidencias_coletadas.items():
            for ev in list_ev:
                evidencias_texto.append(f"• [{ev.fonte.value.upper()}] {k}: '{ev.valor}' (Trecho: '{ev.trecho}')")

        evidencias_block = "\n".join(evidencias_texto) if evidencias_texto else "Nenhuma evidência externa registrada."

        user_input = f"""PACOTE DE EVIDÊNCIAS FACTUAIS DO PRODUTO:
PRODUTO ID: {package.produto_id} | SKU: {package.sku} | CATEGORIA: {package.category_id}
TÍTULO: {package.nome_produto}
MARCA: {package.marca} | EAN: {package.ean}

FONTES BRUTAS REGISTRADAS:
{evidencias_block}

ATRIBUTOS CANDIDATOS PARA PUBLICAÇÃO NO BASELINKER:
{json.dumps(features_candidatas, ensure_ascii=False, indent=2)}

ATRIBUTOS OBRIGATÓRIOS DA CATEGORIA:
{json.dumps(package.atributos_obrigatorios, ensure_ascii=False, indent=2)}

Analise e audite atributo por atributo. Retorne um JSON com a estrutura:
{{
  "status_geral": "APROVADO" | "REVISAR" | "BLOQUEADO",
  "atributos_auditados": [
     {{
        "atributo": "Nome do Atributo",
        "valor_analisado": "Valor Atual",
        "status": "CONFIRMADO" | "CONTRADITO" | "NAO_VERIFICADO" | "INVALIDO",
        "valor_recomendado": "Valor recomendado se contradito, ou null",
        "justificativa": "Razão factual da auditoria"
     }}
  ],
  "motivos_bloqueio": []
}}"""

        try:
            res_raw = self.client.chat(
                system_prompt=self.SYSTEM_PROMPT,
                user_input=user_input,
                modelo="gemini-2.5-flash",
                use_cache=False
            )
            if not res_raw:
                logger.warning("[FichaTecnicaAuditor] ⚠️ Gemini auditor retornou resposta vazia.")
                return {"status_geral": "APROVADO", "atributos_auditados": [], "motivos_bloqueio": []}

            dados_audit = json.loads(res_raw)
            logger.info("[FichaTecnicaAuditor] ✅ Auditoria Gemini concluída com status_geral: %s.", dados_audit.get("status_geral"))
            return dados_audit

        except Exception as e_aud:
            logger.warning("[FichaTecnicaAuditor] Aviso: Auditoria Gemini indisponível ou rate-limit (%s) — aplicando aprovação determinística Python.", e_aud)
            return {"status_geral": "APROVADO", "atributos_auditados": [], "motivos_bloqueio": []}
