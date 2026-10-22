"""
criacao_estruturado/services/ficha_tecnica/validator.py

Validador Determinístico Python (FichaTecnicaValidator).
Valida regras de estrutura, tipos, enums e integridade fática de kits sem dependência de IA.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def is_unidades_por_kit_attribute(nome_campo: str, attr_id: str = "") -> bool:
    """
    Identifica de forma dinâmica se um atributo da ficha técnica do Mercado Livre
    corresponde a 'UNITS_PER_PACK' (Unidades por kit / Embalagem / Quantidade de itens do pacote),
    independentemente do nome customizado da categoria (ex: 'Quantidade de selantes e silicones',
    'Quantidade de frascos', 'Quantidade de peças', 'Unidades por embalagem', etc.).
    """
    if not nome_campo:
        return False

    aid = str(attr_id).strip().upper()
    if aid in ("UNITS_PER_PACK", "PACKAGE_UNITS", "UNITS_PER_PACKAGE", "SALE_UNITS", "UNITS_PER_CONTAINER"):
        return True

    n = str(nome_campo).strip().lower()

    # Expressões exatas ou parciais comuns de unidades por kit
    termos_diretos = [
        "unidades por kit", "unidades por embalagem", "unidades por pacote",
        "unidades por caixa", "unidades por container", "quantidade por kit",
        "quantidade por embalagem", "quantidade de peças", "quantidade de unidades",
        "unidades por kit / embalagem"
    ]
    if any(t in n for t in termos_diretos):
        return True

    # Padrão dinâmico da API do Mercado Livre para categorias específicas: "quantidade de [produto/item]"
    # Ex: "quantidade de selantes e silicones", "quantidade de frascos", "quantidade de bisnagas", "quantidade de latas"
    if n.startswith("quantidade de "):
        excecoes = ["peso", "volume", "portas", "gavetas", "meses", "dias", "anos", "garantia", "ml", "gramas", "kg", "comprimento", "largura", "altura"]
        if not any(exc in n for exc in excecoes):
            return True

    return False


class FichaTecnicaValidator:
    """
    Validador Determinístico em código Python.
    Possui a palavra final sobre a estrutura e regras fáticas dos kits.
    """

    @staticmethod
    def resolver_atributos_fisicos_kit(
        features: Dict[str, str],
        quantidade: int = 1,
        atributos_obrigatorios: Optional[List[str]] = None
    ) -> Dict[str, str]:
        """
        Garante a integridade fática de kits e unidades de forma matemática:
        - Se quantidade > 1: Formato de venda = 'Kit', Quantidade = N, Unidades por kit = N
        - Se quantidade == 1: Formato de venda = 'Unidade', Quantidade = 1, Unidades por kit = 1
        - Mapeia dinamicamente atributos customizados de UNITS_PER_PACK por categoria (ex: Quantidade de selantes e silicones)
        - Condição do item = 'Novo'
        - Tipo de unidade = 'Unidade'
        - Unidade de venda = 'Unidade'
        """
        if not isinstance(features, dict):
            return features

        novas_features = dict(features)
        qtd_str = str(quantidade)
        fmt_venda = "Kit" if quantidade > 1 else "Unidade"

        for k in list(novas_features.keys()):
            k_lower = str(k).strip().lower()

            if k_lower in ("formato de venda", "formato de venda ", "formatodevenda"):
                novas_features[k] = fmt_venda

            if k_lower in ("quantidade", "quantidade ", "quantidade de peças", "quantidade de unidades"):
                novas_features[k] = qtd_str

            if is_unidades_por_kit_attribute(k):
                novas_features[k] = qtd_str

            if k_lower in ("tipo de unidade", "unidade de venda"):
                novas_features[k] = "Unidade"

            if k_lower in ("condição do item", "item_condition"):
                novas_features[k] = "Novo"

        # Garante que atributos obrigatórios especificamente nomeados pela categoria (ex: 'Quantidade de selantes e silicones') recebam o valor da quantidade
        if atributos_obrigatorios:
            for attr in atributos_obrigatorios:
                nome_attr = attr.split(" [")[0].strip()
                if is_unidades_por_kit_attribute(nome_attr):
                    novas_features[nome_attr] = qtd_str

        novas_features["Formato de venda"] = fmt_venda
        novas_features["Unidades por kit"] = qtd_str
        novas_features["Quantidade"] = qtd_str
        novas_features["Condição do item"] = "Novo"
        novas_features["Tipo de unidade"] = "Unidade"
        novas_features["Unidade de venda"] = "Unidade"

        return novas_features

    @staticmethod
    def validar_ficha_tecnica(
        features: Dict[str, str],
        atributos_obrigatorios: List[str]
    ) -> Tuple[bool, List[str]]:
        """
        Executa validações determinísticas objetivas na ficha técnica.
        Retorna (valido, lista_de_erros).
        """
        erros: List[str] = []

        if not isinstance(features, dict):
            erros.append("A ficha técnica precisa ser um dicionário válido.")
            return False, erros

        # 1. Verifica se campos obrigatórios da categoria estão preenchidos
        for attr in atributos_obrigatorios:
            nome_attr = attr.split(" [")[0].strip()
            # Ignora IDs/GTINs gerados automaticamente
            if nome_attr.lower() in ("ean", "gtin", "código de barras", "motivo de gtin vazio", "reason_for_no_gtin"):
                continue

            valor = features.get(nome_attr)
            if not valor or str(valor).strip().lower() in ("null", "n/a", "não informado", "preencher"):
                erros.append(f"Atributo obrigatório ausente: '{nome_attr}'")

        return len(erros) == 0, erros
