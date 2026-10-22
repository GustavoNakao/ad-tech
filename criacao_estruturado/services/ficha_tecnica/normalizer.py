"""
criacao_estruturado/services/ficha_tecnica/normalizer.py

Camada de Normalização, Sanitização e Expurgo Estrito.
Garante tradução de sinônimos canônicos, padronização de unidades mL/g,
fixação de Tipo de Unidade = Unidade e expurgo de chaves fora do formulário.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Set

from .validator import FichaTecnicaValidator, is_unidades_por_kit_attribute

logger = logging.getLogger(__name__)


class FichaTecnicaNormalizer:
    @staticmethod
    def parece_corrompido(texto: str) -> Tuple[bool, str]:
        """
        Rede de segurança estrutural genérica para detectar texto corrompido sem depender de lista fixa de frases.
        Retorna (is_corrupted: bool, razao: str).
        """
        if not texto or not isinstance(texto, str):
            return False, ""

        t = texto.strip()

        # 1. Ponto seguido imediatamente por letra minúscula sem espaço (ex: outros.m, ferro.a)
        if re.search(r'\b[a-zA-Zá-úÁ-Ú]+\.[a-z]\b', t):
            return True, "Ponto final seguido de minúscula sem espaço (ex: outros.m)"

        # 2. Resíduo de palavra cortada no final de frase (ex: 'm valor', 's valor', 'entre outros.')
        if re.search(r'\b(m valor|s valor|entre outros\.)\b', t, re.IGNORECASE):
            return True, "Resíduo de frase cortada ou corrompida"

        # 3. Pontuação solta ou pendente no final de string (diferente de ponto final comum)
        if re.search(r'[,;:]\s*$', t):
            return True, "Pontuação solta pendente"

        # 4. Múltiplos espaços consecutivos anormais
        if re.search(r'\s{3,}', t):
            return True, "Múltiplos espaços anormais detectados"

        return False, ""

    @classmethod
    def normalizar_e_expurgar(
        cls,
        features: Dict[str, str],
        atributos_obrigatorios: List[str]
    ) -> Dict[str, str]:
        if not isinstance(features, dict):
            return {}

        # 1. Monta o conjunto de chaves permitidas da categoria no BaseLinker
        chaves_permitidas: Set[str] = {c.split(" [")[0].strip().lower() for c in atributos_obrigatorios}
        chaves_permitidas.update({
            "marca", "modelo", "cor", "cor principal", "volume líquido", "peso líquido",
            "tipo de base", "superfícies aptas", "formato de venda", "quantidade",
            "unidades por kit", "tipo de unidade", "unidade de venda", "condição do item",
            "altura da embalagem", "largura da embalagem", "comprimento da embalagem", "peso da embalagem"
        })

        novas_features: Dict[str, str] = {}

        for k, v in features.items():
            if not k or v is None:
                continue

            str_v = str(v).strip()
            v_lower = str_v.lower()
            k_lower = str(k).strip().lower()

            # Expulsa placeholders e lixo
            if v_lower in ("não informado", "nao informado", "preencher", "null", "n/a", "none", "", "indeterminado"):
                continue

            # Mapeia sinônimos de catálogo para nomes canônicos do ML
            k_canonico = k.strip()
            if k_lower in ("volume da unidade", "volume liquido", "volume_liquido"):
                k_canonico = "Volume líquido"
            elif k_lower in ("peso da unidade", "peso liquido", "peso_liquido"):
                k_canonico = "Peso líquido"

            # Re-avalia o k_lower após a canonicalização
            k_lower_canonico = k_canonico.lower()

            # Descarta chaves sobressalentes que não pertencem ao formulário da categoria
            if k_lower_canonico not in chaves_permitidas and not is_unidades_por_kit_attribute(k_canonico):
                logger.debug("[FichaTecnicaNormalizer] Expurgo de chave sem formulário na categoria: '%s'", k_canonico)
                continue

            # Check da Rede de Segurança Estrutural (Sem adivinhação: omite se parecer corrompido)
            corrompido, razao = cls.parece_corrompido(str_v)
            if corrompido:
                logger.warning(
                    "[FichaTecnicaNormalizer] Omitindo atributo '%s' com valor suspeito/corrompido ('%s'): %s",
                    k_canonico, str_v, razao
                )
                continue

            # Padronização de unidade de volume para 'mL' com ESPAÇO OBRIGATÓRIO (ex: 350 mL)
            if k_lower_canonico == "volume líquido":
                m_num = re.search(r'(\d+)', str_v)
                if m_num:
                    str_v = f"{m_num.group(1)} mL"
                else:
                    str_v = re.sub(r'(\b\d+)\s*ml\b', r'\1 mL', str_v, flags=re.IGNORECASE)

            # Padronização de unidade de peso para 'g' com ESPAÇO OBRIGATÓRIO (ex: 250 g)
            if k_lower_canonico == "peso líquido":
                m_num = re.search(r'(\d+)', str_v)
                if m_num:
                    str_v = f"{m_num.group(1)} g"

            # Força Tipo de unidade e Unidade de venda como 'Unidade'
            if k_lower_canonico in ("tipo de unidade", "unidade de venda"):
                str_v = "Unidade"

            novas_features[k_canonico] = str_v

        # 3. Trava Estrita de Coerência entre Cor e Cor principal
        if novas_features.get("Cor"):
            cor_val = novas_features["Cor"]
            if cor_val.lower() in ("incolor", "verniz", "transparente"):
                novas_features["Cor principal"] = "Incolor"
            else:
                novas_features["Cor principal"] = cor_val.split()[0].title()

        # 4. Sanitização de Superfícies aptas (remove 'entre outros.m valor', 'erro', etc.)
        if novas_features.get("Superfícies aptas"):
            sup_v = novas_features["Superfícies aptas"]
            sup_v = re.sub(r'[,.]?\s*(entre outros|sem valor|m valor|erro).*$', '', sup_v, flags=re.IGNORECASE).strip()
            sup_v = re.sub(r'\s+,', ',', sup_v)
            novas_features["Superfícies aptas"] = sup_v

        return novas_features
