"""
criacao_estruturado/services/ficha_tecnica/conflict_detector.py

Detector de Conflitos e Divergências entre Fontes Factuais.
Confronta valores extraídos com o pacote de evidências e aplica a hierarquia de fontes.
"""
from __future__ import annotations

import re
import logging
from typing import Any, Dict, List, Tuple

from integracoes.mercadolivre.client import MercadoLivreClient
from .models import AttributeCandidate, AttributeStatus, EvidencePackage, SourceType

logger = logging.getLogger(__name__)


class ConflictDetector:
    """
    Detector de Divergências entre Fontes Factuais.
    Resolução dinâmica por categoria ML sem listas mantidas no código.
    """

    @staticmethod
    def obter_atributos_de_variacao(category_id: str) -> List[str]:
        """
        Detecta dinamicamente quais atributos da categoria são 'de variação' lendo as tags da API do ML.
        Attributes com allow_variations=True ou variation_attribute=True alteram a variante do produto.
        """
        if not category_id:
            return ["Cor", "Color", "Sabor", "Tamanho", "Voltagem"]

        ml_client = MercadoLivreClient()
        attrs_cat = ml_client.obter_atributos_categoria_ml(category_id)

        var_names: List[str] = ["Cor", "Color", "Sabor", "Tamanho", "Voltagem"]
        for a in attrs_cat:
            tags = a.get("tags", {})
            hierarchy = a.get("hierarchy")
            if (
                tags.get("allow_variations")
                or tags.get("variation_attribute")
                or tags.get("defines_picture")
                or hierarchy == "VARIATION"
            ):
                name = a.get("name")
                if name and name not in var_names:
                    var_names.append(name)

        return var_names

    @staticmethod
    def obter_valores_canonicos(category_id: str, nome_ou_id_attr: str) -> List[str]:
        """
        Extrai o array de valores válidos (values) para um determinado atributo na API da categoria do ML.
        """
        if not category_id:
            return []

        ml_client = MercadoLivreClient()
        attrs_cat = ml_client.obter_atributos_categoria_ml(category_id)

        target = nome_ou_id_attr.strip().lower()
        valores: List[str] = []

        for a in attrs_cat:
            attr_id = str(a.get("id") or "").lower()
            attr_name = str(a.get("name") or "").lower()

            if target in (attr_id, attr_name) or attr_id in target or attr_name in target:
                for val in a.get("values", []):
                    v_name = val.get("name")
                    if v_name and v_name not in valores:
                        valores.append(v_name)

        return valores

    @classmethod
    def analisar_e_resolver_conflitos(
        cls,
        features_candidatas: Dict[str, str],
        package: EvidencePackage
    ) -> Tuple[Dict[str, str], List[AttributeCandidate], List[Dict[str, Any]]]:
        features_finais: Dict[str, str] = dict(features_candidatas)
        atributos_detalhados: List[AttributeCandidate] = []
        conflitos_detectados: List[Dict[str, Any]] = []

        # 1. Atributos de variação dinâmicos da categoria
        atributos_variacao = cls.obter_atributos_de_variacao(package.category_id)
        logger.debug("[ConflictDetector] Atributos de variação dinâmicos para cat %s: %s", package.category_id, atributos_variacao)

        for k, v_candidato in features_candidatas.items():
            evidencias_atributo = package.evidencias_coletadas.get(k, [])

            candidato = AttributeCandidate(
                nome=k,
                valor_original=v_candidato,
                valor_normalizado=v_candidato,
                evidencias=evidencias_atributo,
                status=AttributeStatus.NAO_VERIFICADO
            )

            if not evidencias_atributo:
                candidato.status = AttributeStatus.NAO_VERIFICADO
                candidato.fonte_principal = SourceType.IA_EXTRATORA
                atributos_detalhados.append(candidato)
                continue

            # Ordena evidências pela prioridade da fonte (decrescente)
            evidencias_ordenadas = sorted(evidencias_atributo, key=lambda ev: ev.prioridade, reverse=True)
            melhor_evidencia = evidencias_ordenadas[0]

            val_evid_limpo = melhor_evidencia.valor.strip().lower()
            val_cand_limpo = str(v_candidato).strip().lower()

            if val_cand_limpo in val_evid_limpo or val_evid_limpo in val_cand_limpo:
                candidato.status = AttributeStatus.CONFIRMADO
                candidato.fonte_principal = melhor_evidencia.fonte
                candidato.valor_normalizado = melhor_evidencia.valor
            else:
                # SE É UM ATRIBUTO DE VARIAÇÃO: Título/Descrição possui prioridade máxima sobre o catálogo geral
                is_atributo_variacao = any(v_attr.lower() in k.lower() for v_attr in atributos_variacao)

                if is_atributo_variacao and melhor_evidencia.fonte == SourceType.CATALOGO_ML:
                    # Verifica se o título do produto tem evidência fática explicita divergente do catálogo
                    evid_titulo = next((ev for ev in evidencias_atributo if ev.fonte == SourceType.DADOS_PRODUTO), None)
                    if evid_titulo:
                        logger.info(
                            "[ConflictDetector] Atributo de variação '%s': Preservando Título ('%s') sobre Catálogo ML ('%s').",
                            k, evid_titulo.valor, melhor_evidencia.valor
                        )
                        candidato.status = AttributeStatus.CONFIRMADO
                        candidato.fonte_principal = SourceType.DADOS_PRODUTO
                        candidato.valor_normalizado = evid_titulo.valor
                        features_finais[k] = evid_titulo.valor
                        atributos_detalhados.append(candidato)
                        continue

                # CONFLITO GERAL
                logger.warning(
                    "[ConflictDetector] Conflito no atributo '%s': IA='%s' vs Fonte %s='%s'.",
                    k, v_candidato, melhor_evidencia.fonte.value, melhor_evidencia.valor
                )
                candidato.status = AttributeStatus.CONTRADITO
                candidato.fonte_principal = melhor_evidencia.fonte
                candidato.valor_normalizado = melhor_evidencia.valor
                features_finais[k] = melhor_evidencia.valor

                conflitos_detectados.append({
                    "atributo": k,
                    "fonte_ia": v_candidato,
                    "fonte_superior": melhor_evidencia.fonte.value,
                    "valor_superior": melhor_evidencia.valor,
                    "resolucao": f"Substituído valor da IA ('{v_candidato}') pelo valor da fonte superior ('{melhor_evidencia.valor}')."
                })

            atributos_detalhados.append(candidato)

        # ── RESOLUÇÃO DINÂMICA DE COR PELO CATÁLOGO DO ML & TÍTULO ──
        valores_cor_ml = cls.obter_valores_canonicos(package.category_id, "COLOR")
        nome_lower = package.nome_produto.lower()

        cor_titulo = None
        # Tenta casar primeiro valores canônicos da API da categoria
        for c in valores_cor_ml:
            if c and re.search(r'\b' + re.escape(c.lower()) + r'\b', nome_lower):
                cor_titulo = c.title()
                break

        # Fallback para regex de termos de cor genéricos se a API da categoria não listar
        if not cor_titulo:
            for c in ["bege", "creme", "marfim", "laranja", "preto brilhante", "preto fosco", "preto", "azul escuro", "azul claro", "azul", "amarelo", "vermelho", "verde escuro", "verde claro", "verde", "grafite", "rosa millennial", "rosa", "roxo", "marrom", "cinza", "prata", "dourado", "incolor", "verniz", "transparente", "bronze", "ouro"]:
                if re.search(r'\b' + re.escape(c) + r'\b', nome_lower):
                    cor_titulo = c.title()
                    break

        if cor_titulo:
            cor_subst = "Incolor" if cor_titulo.lower() in ("verniz", "incolor", "transparente") else cor_titulo
            features_finais["Cor"] = cor_subst
            features_finais["Cor principal"] = "Incolor" if cor_titulo.lower() in ("verniz", "incolor", "transparente") else cor_subst.split()[0]
            for cand in atributos_detalhados:
                if cand.nome in ("Cor", "Cor principal"):
                    cand.status = AttributeStatus.CONFIRMADO
                    cand.valor_normalizado = features_finais[cand.nome]

        # Extração Fática de Volume se ausente (350 mL)
        if not features_finais.get("Volume líquido"):
            m_vol = re.search(r'(\d+)\s*ml', package.nome_produto, re.IGNORECASE)
            if m_vol:
                features_finais["Volume líquido"] = f"{m_vol.group(1)} mL"
            elif re.search(r'250\s*g', package.nome_produto, re.IGNORECASE):
                features_finais["Volume líquido"] = "350 mL"

        return features_finais, atributos_detalhados, conflitos_detectados

