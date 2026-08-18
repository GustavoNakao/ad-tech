"""
criacao_estruturado/services/ficha_tecnica/ml_fill_service.py

Executa o preenchimento dos atributos obrigatórios da categoria do Mercado Livre
usando IA (Groq por padrão) com prompt gerado a partir do schema enxuto.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List, Optional

from integracoes.groq.client import GroqClient
from .categoria_atributos_service import CategoriaAtributosMLService
from .ml_prompt_service import MLPromptService

logger = logging.getLogger(__name__)


class MLAttributeFillService:
    """Seu ponto de entrada para preencher atributos da categoria do ML via IA."""

    def __init__(self, client: Optional[GroqClient] = None) -> None:
        self.groq = client or GroqClient()
        self.category_service = CategoriaAtributosMLService()

    @staticmethod
    def _normalizar_valor_ia(valor: Any) -> Optional[str]:
        if valor is None:
            return None
        texto = str(valor).strip()
        if texto.lower() in {"", "null", "none", "n/a", "nao informado", "não informado", "preencher"}:
            return None
        return texto

    def resolve_and_prepare(self, categoria_input: str, titulo_produto: str, **contexto) -> Dict[str, Any]:
        payload = self.category_service.build_payload_for_ia(
            categoria_input=categoria_input,
            titulo_produto=titulo_produto,
            contexto_extra=contexto,
        )
        return payload

    def converter_para_attributes_ml(
        self,
        resposta_ia: Dict[str, Any],
        atributos_categoria: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Converte o dicionário retornado pela IA em um array no formato aceito pelo Mercado Livre:
        [{"id": "BRAND", "value_id": "9344", "value_name": None}, ...]
        """
        if not isinstance(resposta_ia, dict):
            return []

        resposta_normalizada = {}
        for chave, valor in resposta_ia.items():
            if chave is None:
                continue
            resposta_normalizada[str(chave)] = valor

        atributos_ml: List[Dict[str, Any]] = []
        resposta_lower = {str(k).lower(): v for k, v in resposta_normalizada.items()}

        for campo in atributos_categoria:
            if not isinstance(campo, dict):
                continue

            attr_id = str(campo.get("id") or "").strip()
            attr_name = str(campo.get("name") or attr_id).strip()
            if not attr_id:
                continue

            valor_raw = None
            for chave in [attr_name, attr_id]:
                if chave in resposta_normalizada:
                    valor_raw = resposta_normalizada.get(chave)
                    break
            if valor_raw is None:
                chave_lower = attr_name.lower()
                if chave_lower in resposta_lower:
                    valor_raw = resposta_lower.get(chave_lower)
                else:
                    chave_id_lower = attr_id.lower()
                    if chave_id_lower in resposta_lower:
                        valor_raw = resposta_lower.get(chave_id_lower)

            valor = self._normalizar_valor_ia(valor_raw)
            item_ml: Dict[str, Any] = {"id": attr_id}

            if valor is None:
                item_ml["unresolved"] = True
                item_ml["value_id"] = None
                item_ml["value_name"] = None
                atributos_ml.append(item_ml)
                continue

            valores_permitidos = campo.get("values") or []
            valor_id = None
            valor_nome = None

            for opcao in valores_permitidos:
                if isinstance(opcao, dict):
                    nome_op = str(opcao.get("name") or opcao.get("value_name") or "").strip()
                    id_op = str(opcao.get("id") or opcao.get("value_id") or "").strip()
                else:
                    nome_op = str(opcao).strip()
                    id_op = ""

                if not nome_op:
                    continue

                if str(valor).strip().lower() == nome_op.lower():
                    valor_id = id_op or None
                    valor_nome = None
                    break

            if valor_id:
                item_ml["value_id"] = valor_id
                item_ml["value_name"] = None
            else:
                item_ml["value_id"] = None
                item_ml["value_name"] = str(valor)

            atributos_ml.append(item_ml)

        return atributos_ml

    def fill_attributes(
        self,
        categoria_input: str,
        titulo_produto: str,
        descricao_produto: str = "",
        marca: str = "",
        ean: str = "",
        imagens: Optional[List[str]] = None,
        dados_internos: Optional[Dict[str, Any]] = None,
        **contexto,
    ) -> Dict[str, Any]:
        """
        Resolve a categoria, monta schema enxuto, gera o prompt e pede a IA preencher JSON.
        Retorna o payload final pronto para uso.
        """
        payload = self.resolve_and_prepare(categoria_input, titulo_produto, **contexto)
        category_id = payload.get("category_id")
        campos = payload.get("field_names") or []
        schema = payload.get("schema_ia")

        if not category_id:
            return {
                "category_id": None,
                "atributos": {},
                "erro": "Categoria não resolvida",
            }

        prompt = MLPromptService.build_prompt(
            category_id=str(category_id),
            titulo_produto=titulo_produto,
            descricao_produto=descricao_produto,
            marca=marca,
            ean=ean,
            imagens=imagens,
            dados_internos=dados_internos,
            campos_permitidos=campos,
            schema_ia=schema,
        )

        try:
            resposta = self.groq.chat(
                system_prompt="Você deve responder somente JSON válido, sem texto extra.",
                user_input=prompt,
                modelo="llama-3.1-8b-instant",
                temperature=0.0,
            )
            if not resposta:
                return {"category_id": category_id, "atributos": {}, "erro": "IA sem resposta"}

            dados = json.loads(resposta)
            if not isinstance(dados, dict):
                return {"category_id": category_id, "atributos": {}, "erro": "Resposta da IA não é dicionário JSON"}

            atributos_ok = {}
            for chave in campos:
                valor = dados.get(chave)
                if valor is not None and str(valor).strip() not in ("", "null", "None", "N/A", "Não informado", "não informado"):
                    atributos_ok[chave] = valor

            atributos_ml = self.converter_para_attributes_ml(
                resposta_ia=atributos_ok,
                atributos_categoria=payload.get("attributes") or [],
            )

            return {
                "category_id": category_id,
                "atributos": atributos_ok,
                "attributes_ml": atributos_ml,
                "schema_ia": schema,
                "prompt": prompt,
            }

        except Exception as exc:  # pragma: no cover - guard para uso em runtime
            logger.warning("Erro ao preencher atributos via IA para categoria %s: %s", category_id, exc)
            return {
                "category_id": category_id,
                "atributos": {},
                "attributes_ml": [],
                "erro": str(exc),
                "schema_ia": schema,
                "prompt": prompt,
            }
