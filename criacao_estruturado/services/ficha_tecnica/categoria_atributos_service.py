"""
criacao_estruturado/services/ficha_tecnica/categoria_atributos_service.py

Orquestra a coleta dos atributos relevantes de uma categoria do Mercado Livre,
filtrando obrigatórios/condicionais/soft e montando um payload enxuto para a IA.

Fluxo implementado:
1. resolve category_id
2. coleta atributos reais da categoria
3. filtra hard / conditional / soft
4. monta schema enxuto para a IA

Endpoints do ML envolvidos:
- GET /sites/{site_id}/domain_discovery/search?q={titulo}
- GET /categories/{category_id}/attributes
- GET /categories/{category_id}/technical_specs/input
- POST /categories/{category_id}/attributes/conditional (quando suportado)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from integracoes.mercadolivre.client import MercadoLivreClient
from .schemas import SchemaGenerator

logger = logging.getLogger(__name__)


class CategoriaAtributosMLService:
    """Resolve categoria e monta o payload enxuto para IA preencher atributos do ML."""

    def __init__(self, client: Optional[MercadoLivreClient] = None) -> None:
        self.ml = client or MercadoLivreClient()

    def resolve_category_id(self, categoria_input: str, titulo_produto: str = "") -> Optional[str]:
        """Resolve category_id a partir da categoria do usuário ou do título."""
        if not categoria_input or not str(categoria_input).strip():
            categoria_input = ""

        category_id = self.ml.resolver_categoria_por_input(str(categoria_input).strip()) if categoria_input else None
        if category_id:
            return category_id

        if titulo_produto:
            resultados = self.ml.predict_categoria(titulo_produto, limite=5)
            if resultados:
                category_id = resultados[0].get("category_id")
                if category_id:
                    return str(category_id)

        return None

    @staticmethod
    def _normalizar_valores(values: Any) -> List[str]:
        if not isinstance(values, list):
            return []
        saida: List[str] = []
        for item in values:
            if isinstance(item, dict):
                valor = item.get("name") or item.get("value_name") or item.get("id")
            else:
                valor = item
            if valor is not None and str(valor).strip():
                saida.append(str(valor).strip())
        return saida

    def _coletar_atributos_hard(self, category_id: str) -> Dict[str, Dict[str, Any]]:
        attrs = self.ml.ficha_tecnica(category_id, apenas_obrigatorios=False) or []
        mapa: Dict[str, Dict[str, Any]] = {}

        for item in attrs:
            campo_id = str(item.get("id") or "").strip()
            if not campo_id:
                continue

            nome = str(item.get("name") or campo_id).strip()
            required = bool(item.get("required"))
            values = self._normalizar_valores(item.get("values", []))

            mapa[campo_id] = {
                "id": campo_id,
                "name": nome,
                "required": required,
                "required_type": "hard" if required else "optional",
                "values": values,
                "source": "attributes",
            }

        return mapa

    def _coletar_atributos_soft(self, category_id: str) -> Dict[str, Dict[str, Any]]:
        specs = self.ml.technical_specs_input(category_id) or []
        mapa: Dict[str, Dict[str, Any]] = {}

        for item in specs:
            campo_id = str(item.get("id") or "").strip()
            if not campo_id:
                continue

            required = bool(item.get("required"))
            if not required:
                continue

            nome = str(item.get("name") or campo_id).strip()
            mapa[campo_id] = {
                "id": campo_id,
                "name": nome,
                "required": True,
                "required_type": "soft",
                "values": self._normalizar_valores(item.get("values", [])),
                "source": "technical_specs_input",
            }

        return mapa

    def _coletar_atributos_condicionais(self, category_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
        if not hasattr(self.ml, "atributos_condicionais"):
            return {}

        resposta = self.ml.atributos_condicionais(category_id, payload or {}) or []
        if not resposta:
            return {}

        mapa: Dict[str, Dict[str, Any]] = {}
        for item in resposta:
            campo_id = str(item.get("id") or "").strip()
            if not campo_id:
                continue
            nome = str(item.get("name") or campo_id).strip()
            mapa[campo_id] = {
                "id": campo_id,
                "name": nome,
                "required": True,
                "required_type": "conditional",
                "values": self._normalizar_valores(item.get("values", [])),
                "source": "attributes_conditional",
            }

        return mapa

    def _merge_atributos(self, *maps: Iterable[Dict[str, Dict[str, Any]]]) -> List[Dict[str, Any]]:
        final: Dict[str, Dict[str, Any]] = {}

        for mapa in maps:
            for campo_id, attr in (mapa or {}).items():
                if campo_id in final:
                    atual = final[campo_id]
                    atual["required_type"] = atual["required_type"] if atual["required_type"] in ("hard", "conditional", "soft") else attr["required_type"]
                    atual["required"] = atual.get("required") or attr.get("required")
                    if not atual.get("values"):
                        atual["values"] = attr.get("values", [])
                    if not atual.get("source"):
                        atual["source"] = attr.get("source")
                    continue
                final[campo_id] = dict(attr)

        ordem = {"hard": 0, "conditional": 1, "soft": 2, "optional": 3}
        return sorted(final.values(), key=lambda item: (ordem.get(str(item.get("required_type", "optional")).lower(), 99), str(item.get("name") or item.get("id")).lower()))

    def montar_schema_ia(self, atributos: List[Dict[str, Any]]) -> Dict[str, Any]:
        nomes = [str(item.get("name") or item.get("id") or "").strip() for item in atributos if (item.get("name") or item.get("id"))]
        nomes = [n for n in nomes if n]
        return SchemaGenerator.gerar_json_schema_estrito(nomes)

    def build_payload_for_ia(
        self,
        categoria_input: str,
        titulo_produto: str = "",
        contexto_extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Agrega category_id + atributos relevantes + schema enxuto para a IA.
        Retorna payload ready-to-use para o next step.
        """
        category_id = self.resolve_category_id(categoria_input, titulo_produto)
        if not category_id:
            return {
                "category_id": None,
                "category_input": categoria_input,
                "titulo_produto": titulo_produto,
                "attributes": [],
                "field_names": [],
                "schema_ia": {"name": "ficha_tecnica_estrita", "strict": True, "schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}},
                "erro": "Categoria do Mercado Livre não encontrada",
                "contexto_extra": contexto_extra or {},
            }

        hard = self._coletar_atributos_hard(category_id)
        soft = self._coletar_atributos_soft(category_id)
        conditional = self._coletar_atributos_condicionais(category_id, contexto_extra or {})

        atributos = self._merge_atributos(hard, conditional, soft)

        # Remove os opcionais e mantém só itens realmente relevantes para a IA
        atributos_relevantes = [
            attr for attr in atributos
            if str(attr.get("required_type", "optional")).lower() in {"hard", "conditional", "soft"}
        ]

        field_names = [str(attr.get("name") or attr.get("id") or "").strip() for attr in atributos_relevantes if (attr.get("name") or attr.get("id"))]
        field_names = [n for n in field_names if n]

        return {
            "category_id": str(category_id),
            "category_input": categoria_input,
            "titulo_produto": titulo_produto,
            "attributes": atributos_relevantes,
            "field_names": field_names,
            "schema_ia": self.montar_schema_ia(atributos_relevantes),
            "contexto_extra": contexto_extra or {},
        }
