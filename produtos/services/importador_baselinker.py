"""
produtos/services/importador_baselinker.py

Sincroniza os dados que o BaseLinker retorna com o model Produto.

O que este service FAZ:
    - Busca SKU no BaseLinker (getInventoryProductsList + getInventoryProductsData)
    - Salva/atualiza: id_baselinker, nome, ncm, marca, codigo_origem,
      peso_kg, altura_cm, largura_cm, comprimento_cm, imagem_url

O que este service NÃO FAZ:
    - Não toca em custo_bruto_nf (vem do ERP/time, não do BL)
    - Não toca em ean (responsabilidade do GeradorEANService)
    - Não toca em preco_concorrente (input manual do time)
    - Não calcula preço (responsabilidade do PricingEngine)
"""
from __future__ import annotations

import logging
from typing import Optional

from produtos.models import Produto

logger = logging.getLogger("produtos.importador")


class ImportadorBaseLinker:
    """
    Uso:
        from produtos.services.importador_baselinker import ImportadorBaseLinker

        resultado = ImportadorBaseLinker().importar(sku="30565")
    """

    def __init__(self):
        from integracoes.baselinker.client import BaselinkerClient
        self.bl = BaselinkerClient()

    def importar(self, sku: str) -> dict:
        """
        Busca o SKU no BaseLinker e faz upsert no banco local.
        Preserva: custo_bruto_nf, ean, preco_concorrente, link_concorrente.

        Retorna dict com sucesso, produto e se foi criado ou atualizado.
        """
        logger.info(f"Importando SKU: {sku}")

        dados_bl = self.bl.buscar_produto_por_sku(sku)
        if not dados_bl:
            return {
                "sucesso": False,
                "erro":    f"SKU '{sku}' não encontrado no BaseLinker.",
            }

        produto, criado = self._upsert(sku, dados_bl)

        acao = "Criado" if criado else "Atualizado"
        logger.info(f"{acao}: [{sku}] {produto.nome}")

        return {
            "sucesso":  True,
            "produto":  produto,
            "criado":   criado,
            "sku":      sku,
        }

    def _upsert(self, sku: str, dados_bl: dict):
        """
        Faz update_or_create pelo SKU.
        Campos preservados (não sobrescritos): custo_bruto_nf, ean,
        preco_concorrente, link_concorrente.
        """
        defaults = {
            "id_baselinker":  str(dados_bl.get("id_produto") or ""),
            "nome":           dados_bl.get("nome") or sku,
            "ncm":            dados_bl.get("ncm") or None,
            "marca":          dados_bl.get("marca") or None,
            "codigo_origem":  dados_bl.get("codigo_origem") or "076",
            "imagem_url":     dados_bl.get("imagem_url") or None,
            "peso_kg":        self._decimal(dados_bl.get("peso_kg")),
            "altura_cm":      self._decimal(dados_bl.get("altura_cm")),
            "largura_cm":     self._decimal(dados_bl.get("largura_cm")),
            "comprimento_cm": self._decimal(dados_bl.get("comprimento_cm")),
        }

        produto, criado = Produto.objects.update_or_create(sku=sku, defaults=defaults)
        return produto, criado

    @staticmethod
    def _decimal(valor) -> Optional[float]:
        if valor is None:
            return None
        try:
            f = float(str(valor).replace(",", "."))
            return f if f > 0 else None
        except (ValueError, TypeError):
            return None
