"""
criacao_estruturado/services/ficha_tecnica/extractor.py

Agente 4 — Extrator Factual (Groq gpt-oss-120b / llama-3.3-70b-versatile).
Extrai atributos candidatos com Structured Outputs (strict: true).
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

from integracoes.groq.client import GroqClient
from .models import EvidencePackage
from .schemas import SchemaGenerator

logger = logging.getLogger(__name__)


class FichaTecnicaExtractor:
    """
    Agente 4 — Extrator Factual via Groq.
    Extrai EXCLUSIVAMENTE informações comprovadas no EvidencePackage.
    Substitui null por ausência sem inventar 'Não informado' ou suposições.
    """

    MODELO_PRIMARIO = "openai/gpt-oss-120b"
    MODELO_FALLBACK = "llama-3.3-70b-versatile"

    SYSTEM_PROMPT = """Você é o Agente 4 — Extrator Factual de Ficha Técnica para Mercado Livre e BaseLinker.
Sua única responsabilidade é extrair atributos técnicos a partir das EVIDÊNCIAS FACTUAIS fornecidas.

REGRAS RÍGIDAS DE FIDEDIGNIDADE:
1. Extraia EXCLUSIVAMENTE informações explícitas presentes nas evidências fornecidas.
2. Se uma informação NÃO estiver comprovada no texto, retorne NULO (null) para aquela chave.
3. É TERMINANTEMENTE PROIBIDO:
   - Adivinhar ou supor valores plausíveis (ex: supor que verniz é Solvente).
   - Injetar 'Não informado', 'PREENCHER', 'null', 'N/A' ou valores vazios.
   - Inventar dados de embalagem ou especificações não citadas.
4. Responda ESTRITAMENTE em formato JSON cumprindo o schema exigido."""

    def __init__(self) -> None:
        self.client = GroqClient()

    def extrair_atributos_candidatos(
        self,
        package: EvidencePackage,
        atributos_ausentes: List[str]
    ) -> Dict[str, Optional[str]]:
        if not atributos_ausentes:
            return {}

        schema_struct = SchemaGenerator.gerar_json_schema_estrito(atributos_ausentes)

        # Monta o bloco de contexto fático a partir do EvidencePackage
        linhas_contexto = [
            f"PRODUTO: {package.nome_produto}",
            f"MARCA: {package.marca}",
            f"EAN: {package.ean}",
            f"CATEGORIA ML: {package.category_id}",
            "--- EVIDÊNCIAS DE CATALOGO E TEXTO ---"
        ]

        for k, evid_list in package.evidencias_coletadas.items():
            for ev in evid_list:
                linhas_contexto.append(f"[{ev.fonte.value.upper()}] {k}: {ev.valor} (Trecho: {ev.trecho})")

        contexto_text = "\n".join(linhas_contexto)
        user_input = f"""Analise as evidências abaixo e preencha os atributos técnicos correspondentes:

{contexto_text}

ATRIBUTOS A EXTRAIR SE COMPROVADOS:
{json.dumps(atributos_ausentes, ensure_ascii=False, indent=2)}"""

        # Tenta chamar o Groq primeiro com modelo gpt-oss-120b e fallback para llama-3.3-70b-versatile
        res_raw = None
        for mod in [self.MODELO_PRIMARIO, self.MODELO_FALLBACK]:
            try:
                res_raw = self.client.chat(
                    system_prompt=self.SYSTEM_PROMPT,
                    user_input=user_input,
                    modelo=mod,
                    temperature=0.0
                )
                if res_raw:
                    logger.info("[FichaTecnicaExtractor] ✅ Sucesso no extrator Groq com modelo %s.", mod)
                    break
            except Exception as e_groq:
                logger.warning("[FichaTecnicaExtractor] Falha modelo %s: %s", mod, e_groq)

        if not res_raw:
            logger.warning("[FichaTecnicaExtractor] ⚠️ Groq não retornou texto no extrator.")
            return {}

        try:
            dados = json.loads(res_raw)
            atribs_extraidos: Dict[str, Optional[str]] = {}
            for k, v in dados.items():
                if v is not None and str(v).strip() and str(v).strip().lower() not in ("null", "n/a", "não informado", "preencher"):
                    atribs_extraidos[k] = str(v).strip()
            return atribs_extraidos
        except json.JSONDecodeError as e_json:
            logger.warning("[FichaTecnicaExtractor] Erro parse JSON extrator: %s", e_json)
            return {}
