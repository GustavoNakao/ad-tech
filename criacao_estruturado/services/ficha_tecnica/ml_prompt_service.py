"""
criacao_estruturado/services/ficha_tecnica/ml_prompt_service.py

Gera o prompt para a IA preencher os atributos relevantes da categoria do Mercado Livre,
respeitando o schema do ML e retornando JSON limpo.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MLPromptService:
    """Builda prompt e estrutura de resposta para o preenchimento de atributos do ML."""

    @staticmethod
    def build_prompt(
        category_id: str,
        titulo_produto: str,
        descricao_produto: str = "",
        marca: str = "",
        ean: str = "",
        imagens: Optional[List[str]] = None,
        dados_internos: Optional[Dict[str, Any]] = None,
        campos_permitidos: Optional[List[str]] = None,
        schema_ia: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Gera o prompt final para a IA responder em JSON compatível com o schema do ML."""
        campos = campos_permitidos or []
        schema = schema_ia or {
            "name": "ficha_tecnica_estrita",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {campo: {"type": ["string", "null"]} for campo in campos},
                "required": list(campos),
                "additionalProperties": False,
            },
        }

        context = {
            "category_id": str(category_id or ""),
            "titulo": titulo_produto or "",
            "descricao": descricao_produto or "",
            "marca": marca or "",
            "ean": ean or "",
            "imagens": imagens or [],
            "dados_internos": dados_internos or {},
        }

        prompt = f"""Você é um extrator de ficha técnica do Mercado Livre.

Objetivo:
Preencher somente os atributos que forem comprovados no contexto do produto e que pertencem à categoria do ML.

Contexto do produto:
{json.dumps(context, ensure_ascii=False, indent=2)}

Atributos permitidos para esta categoria:
{json.dumps(campos, ensure_ascii=False, indent=2)}

Regras obrigatórias:
1. Responda APENAS em JSON válido.
2. Não invente valores.
3. Não inclua chaves fora da lista acima.
4. Se o valor não estiver comprovado no contexto, use null.
5. Não use textos como 'Não informado', 'N/A', 'preencher' ou 'null'. Use null.
6. Para atributos em lista de opções, use somente um valor que exista no contexto ou na categoria ML.
7. Para atributos numéricos ou de unidade, mantenha o formato aceito pelo ML, como '250 ml', '1.2 kg', '12 meses'.
8. Não responda em texto livre fora do JSON.
9. A resposta deve obedecer EXATAMENTE ao schema abaixo.

Schema do JSON esperado:
{json.dumps(schema, ensure_ascii=False, indent=2)}

Retorne um objeto JSON que preencha apenas os atributos permitidos e use null quando não tiver certeza.
"""
        return prompt

    @staticmethod
    def build_json_response_example(campos_permitidos: Optional[List[str]] = None) -> Dict[str, Any]:
        """Exemplo mínimo de resposta JSON esperado pela IA."""
        campos = campos_permitidos or []
        return {campo: None for campo in campos}
