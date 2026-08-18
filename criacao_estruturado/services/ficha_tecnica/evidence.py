"""
criacao_estruturado/services/ficha_tecnica/evidence.py

Coletor de Evidências Factuais e Hierarquia de Fontes.
Constrói o EvidencePackage estruturado antes da extração e auditoria.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from integracoes.mercadolivre.client import MercadoLivreClient
from .models import Evidence, EvidencePackage, SourceType

logger = logging.getLogger(__name__)


class EvidenceCollector:
    """
    Coleta e organiza evidências de múltiplas fontes factuais segundo a hierarquia:
    1. Catálogo Oficial Mercado Livre (Prioridade 100)
    2. Fabricante / Documentação Oficial (Prioridade 95)
    3. Banco Interno ERP / NCM / Cadastro (Prioridade 90)
    4. Informação Explícita do Produto (Prioridade 75)
    5. Descrição SEO (Prioridade 50)
    """

    def __init__(self) -> None:
        self.ml_client = MercadoLivreClient()

    def coletar_pacote_evidencias(
        self,
        produto_id: str,
        sku: str,
        category_id: str,
        nome_produto: str,
        descricao_produto: str = "",
        ean: str = "",
        marca: str = "",
        atributos_obrigatorios: Optional[List[str]] = None,
        dados_internos: Optional[Dict[str, Any]] = None
    ) -> EvidencePackage:
        atributos_obrigatorios = atributos_obrigatorios or []
        dados_internos = dados_internos or {}

        package = EvidencePackage(
            produto_id=produto_id,
            sku=sku,
            category_id=category_id,
            nome_produto=nome_produto,
            descricao_produto=descricao_produto,
            ean=ean,
            marca=marca,
            atributos_obrigatorios=atributos_obrigatorios,
            dados_internos=dados_internos
        )

        # 1. FOntE 1: CATÁLOGO OFICIAL DO MERCADO LIVRE (Prioridade 100)
        try:
            atribs_catalogo = self.ml_client.buscar_atributos_catalogo_ml(
                ean=ean,
                termo_busca=nome_produto,
                marca=marca,
                category_id=category_id
            )
            if atribs_catalogo:
                logger.info("[EvidenceCollector] ✅ %d atributos resgatados do Catálogo Oficial ML na Categoria %s.", len(atribs_catalogo), category_id)
                for k, v in atribs_catalogo.items():
                    if v and str(v).strip():
                        ev = Evidence(
                            fonte=SourceType.CATALOGO_ML,
                            valor=str(v).strip(),
                            trecho=f"Atributo {k} = {v} no produto oficial do catálogo ML",
                            prioridade=100
                        )
                        package.evidencias_coletadas.setdefault(k, []).append(ev)
        except Exception as e_cat:
            logger.warning("[EvidenceCollector] Falha ao coletar catálogo ML: %s", e_cat)

        # 2. FONTE 2: BANCO DE DADOS INTERNO E CADASTRO DO SKU (Prioridade 90)
        for field_name in ("peso", "altura", "largura", "comprimento", "ncm", "marca"):
            val_int = dados_internos.get(field_name) or dados_internos.get(f"features_{field_name}")
            if val_int:
                ev = Evidence(
                    fonte=SourceType.BANCO_INTERNO,
                    valor=str(val_int).strip(),
                    trecho=f"Campo cadastrado {field_name}: {val_int}",
                    prioridade=90
                )
                package.evidencias_coletadas.setdefault(field_name.capitalize(), []).append(ev)

        # 3. FONTE 3: DADOS EXPLÍCITOS DO TÍTULO E TEXTO DO PRODUTO (Prioridade 75)
        if nome_produto:
            ev = Evidence(
                fonte=SourceType.DADOS_PRODUTO,
                valor=nome_produto,
                trecho=f"Título oficial do produto: {nome_produto}",
                prioridade=75
            )
            package.evidencias_coletadas.setdefault("Nome do Produto", []).append(ev)

        # 4. FONTE 4: DESCRIÇÃO DO PRODUTO (Prioridade 50)
        if descricao_produto:
            ev = Evidence(
                fonte=SourceType.DESCRICAO_SEO,
                valor=descricao_produto[:300],
                trecho=f"Trecho inicial da descrição: {descricao_produto[:150]}...",
                prioridade=50
            )
            package.evidencias_coletadas.setdefault("Descrição", []).append(ev)

        return package
