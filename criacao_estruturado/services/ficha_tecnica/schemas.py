"""
criacao_estruturado/services/ficha_tecnica/schemas.py

Gerador de JSON Schemas estritos (Structured Outputs com strict: true)
para as chamadas às IAs (Groq / OpenAI / Gemini).
"""
from __future__ import annotations

from typing import Any, Dict, List


class SchemaGenerator:
    @staticmethod
    def gerar_json_schema_estrito(atributos_obrigatorios: List[str]) -> Dict[str, Any]:
        """
        Gera um JSON Schema 100% válido para Structured Outputs (strict: true).
        
        Regras do OpenAI/Groq Structured Outputs:
        - required DEVE conter TODAS as propriedades declaradas.
        - additionalProperties DEVE ser false.
        - Propriedades potencialmente nulas usam type: ["string", "null"].
        """
        properties: Dict[str, Any] = {}
        required: List[str] = []

        # Chaves padrão garantidas em qualquer ficha técnica
        chaves_base = [
            "Marca", "Modelo", "Cor", "Cor principal", "Volume líquido",
            "Peso líquido", "Superfícies aptas", "Tipo de base"
        ]
        
        # Junta chaves padrão + atributos da categoria
        todas_chaves = list(chaves_base)
        for attr in atributos_obrigatorios:
            nome_limpo = attr.split(" [")[0].strip()
            if nome_limpo not in todas_chaves:
                todas_chaves.append(nome_limpo)

        for chave in todas_chaves:
            properties[chave] = {
                "type": ["string", "null"],
                "description": f"Valor factual para o atributo {chave}. Retorne null se não comprovado."
            }
            required.append(chave)

        schema = {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False
        }

        return {
            "name": "ficha_tecnica_estrita",
            "strict": True,
            "schema": schema
        }
