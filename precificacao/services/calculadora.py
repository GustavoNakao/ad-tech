"""
precificacao/services/calculadora.py

Interface pública do motor de precificação R831 v2.
Todos os apps devem usar esta classe — nunca chamar o engine diretamente.
"""
from __future__ import annotations

import logging
from typing import Optional

from precificacao.engine import ConfigEngine, PricingEngine, ResultadoGrade, StatusMargem

logger = logging.getLogger("precificacao.calculadora")

_engine_cache: Optional[PricingEngine] = None


def _get_engine(category_id: Optional[str] = None) -> PricingEngine:
    global _engine_cache

    from precificacao.models.regras import RegrasPrecificacao, CategoriaComissao

    regras = RegrasPrecificacao.get_ativa()

    categoria_comissao = None
    if category_id:
        categoria_comissao = CategoriaComissao.objects.filter(
            category_id=category_id, ativo=True
        ).first()

    # Cache só para configuração padrão (sem category_id)
    if not category_id:
        if _engine_cache is None:
            _engine_cache = PricingEngine(ConfigEngine.from_regras(regras))
        return _engine_cache

    return PricingEngine(ConfigEngine.from_regras(regras, categoria_comissao))


class Calculadora:
    """
    Interface pública do módulo de precificação R831 v2.
    """

    # ── Grade completa (8 anúncios) ───────────────────────────────────────────

    @staticmethod
    def calcular_grade(
        dados: dict,
        sku_ref: str = "???",
        category_id: Optional[str] = None,
        custo_saida_transportadora: Optional[float] = None,
        vendas_60_dias:             int = 0,
    ) -> ResultadoGrade:
        """
        Gera a grade de 8 anúncios (1/2/3/4un × Clássico/Premium).

        Args:
            dados:                      dict do produto (formato BaseLinker)
            sku_ref:                    SKU para logs
            category_id:                category_id do ML para buscar comissão correta
            custo_saida_transportadora: obrigatório se peso > 30kg

        Returns:
            ResultadoGrade com 8 ResultadoAnuncio
        """
        engine = _get_engine(category_id)
        return engine.calcular_grade(
            dados,
            sku_ref=sku_ref,
            custo_saida_transportadora=custo_saida_transportadora,
            vendas_60_dias=vendas_60_dias
        )

    # ── Cálculo simples (compatibilidade) ─────────────────────────────────────

    @staticmethod
    def calcular(
        dados: dict,
        sku_ref: str = "???",
        category_id: Optional[str] = None,
        vendas_60_dias: int = 0,
    ) -> "ResultadoPrecoSimples":
        """
        Cálculo simplificado para compatibilidade com o restante do sistema.
        Retorna preço de promoção unitário Clássico.
        """
        engine = _get_engine(category_id)
        grade  = engine.calcular_grade(dados, sku_ref=sku_ref, vendas_60_dias=vendas_60_dias)

        if grade.bloqueado or not grade.anuncios:
            from precificacao.models.regras import RegrasPrecificacao
            regras = RegrasPrecificacao.get_ativa()
            
            # Se bloqueado, usa o piso forçado mas escala para Vitrine
            preco_promo = float(regras.piso_valor_forcado)
            preco_vit   = round(preco_promo / 0.80, 2)
            
            return ResultadoPrecoSimples(
                preco_final        = preco_vit,
                custo_detectado    = 0.0,
                tax_rate_detectado = 0.0,
                comissao_usada     = 0.0,
                margem_usada       = 0.12,
                frete_usado        = 0.0,
                piso_aplicado      = True,
            )

        unit = next((a for a in grade.anuncios if a.referencia == "unit_1_classico"), grade.anuncios[0])
        
        # O BaseLinker deve receber o Preço de Vitrine (Hub).
        # A promoção de 20% no ML levará o preço de volta para a Promoção (Floor).
        return ResultadoPrecoSimples(
            preco_final        = unit.preco_vitrine,
            custo_detectado    = engine._extrair_custo(dados),
            tax_rate_detectado = unit.tax_rate_usado,
            comissao_usada     = unit.comissao_usada,
            margem_usada       = engine.config.margem_minima,
            frete_usado        = unit.custo_logistico,
            piso_aplicado      = unit.preco_promocao == engine.config.piso_valor_forcado,
        )

    # ── Análise vs concorrente ────────────────────────────────────────────────

    @staticmethod
    def analisar(
        dados: dict,
        preco_concorrente: float,
        sku_ref: str = "???",
        category_id: Optional[str] = None,
    ) -> "ResultadoAnalise":
        """
        Compara preço de promoção unitário Clássico com o concorrente.
        Retorna status verde/amarelo/vermelho.
        """
        from precificacao.models.regras import RegrasPrecificacao
        engine  = _get_engine(category_id)
        grade   = engine.calcular_grade(dados, sku_ref=sku_ref)
        regras  = RegrasPrecificacao.get_ativa()
        cfg     = engine.config

        if grade.bloqueado or not grade.anuncios:
            return ResultadoAnalise(
                preco_sugerido    = float(regras.piso_valor_forcado),
                status            = StatusMargem.VERMELHO,
                estrategia        = f"🔴 BLOQUEADO: {grade.motivo_bloqueio}",
                margem_real       = 0.0,
                preco_concorrente = preco_concorrente,
            )

        unit = next((a for a in grade.anuncios if a.referencia == "unit_1_classico"), grade.anuncios[0])
        custo_liquido   = engine._extrair_custo(dados)
        impostos_e_taxa = cfg.comissao_classico + unit.tax_rate_usado + cfg.trava_fiscal

        if preco_concorrente > 0:
            margem_real = 1.0 - (custo_liquido / preco_concorrente) - impostos_e_taxa
        else:
            margem_real = 0.0

        if margem_real >= cfg.margem_minima:
            status     = StatusMargem.VERDE
            preco_sug  = round(preco_concorrente - 0.10, 2)
            estrategia = f"🟢 BATER PREÇO: Margem saudável ({margem_real:.1%}). Anunciar R${preco_sug:.2f}."
        elif margem_real >= 0.05:
            status     = StatusMargem.AMARELO
            preco_sug  = round(preco_concorrente, 2)
            estrategia = (
                f"🟡 ANTI-ANÚNCIO: Margem apertada ({margem_real:.1%}). "
                "Igualar preço e destacar benefícios/qualidade."
            )
        else:
            status    = StatusMargem.VERMELHO
            preco_sug = unit.preco_promocao
            estrategia = (
                f"🔴 INVIÁVEL: Margem real {margem_real:.1%}. "
                f"NÃO CADASTRAR. Preço mínimo de sobrevivência: R${preco_sug:.2f}."
            )

        return ResultadoAnalise(
            preco_sugerido    = preco_sug,
            status            = status,
            estrategia        = estrategia,
            margem_real       = round(margem_real, 4),
            preco_concorrente = preco_concorrente,
        )

    # ── Cascade (compatibilidade) ─────────────────────────────────────────────

    @staticmethod
    def cascade_unitario_kit(dados: dict, sku_ref: str = "???") -> dict:
        """
        Retorna preços de promoção para 1/2/3 unidades Clássico.
        Mantido para compatibilidade com o criacao_estruturado.
        """
        engine = _get_engine()
        grade  = engine.calcular_grade(dados, sku_ref=sku_ref)
        cfg    = engine.config

        def _preco(ref: str) -> float:
            a = next((x for x in grade.anuncios if x.referencia == ref), None)
            if a and not a.bloqueado:
                return a.preco_vitrine
            return round(float(cfg.piso_valor_forcado) / 0.80, 2)

        p1 = _preco("unit_1_classico")
        p2 = _preco("unit_2_classico")
        p3 = _preco("unit_3_classico")

        delta12 = p2 - p1
        alerta_upsell = None
        if 0 < delta12 < 10.00:
            alerta_upsell = (
                f"⚡ UPSELL: Kit 2 (R${p2:.2f}) está apenas R${delta12:.2f} "
                f"acima do Unitário (R${p1:.2f}). Destaque no anúncio!"
            )

        return {
            "unitario":      ResultadoPrecoSimples(p1, engine._extrair_custo(dados), 0, 0, 0.12, 0, p1 == cfg.piso_valor_forcado),
            "kit_2":         ResultadoPrecoSimples(p2, engine._extrair_custo(dados) * 2, 0, 0, 0.12, 0, False),
            "kit_3":         ResultadoPrecoSimples(p3, engine._extrair_custo(dados) * 3, 0, 0, 0.12, 0, False),
            "alerta_upsell": alerta_upsell,
        }

    @staticmethod
    def limpar_cache() -> None:
        global _engine_cache
        _engine_cache = None
        logger.info("Cache do PricingEngine invalidado.")


# ── Dataclasses de resultado simplificado ────────────────────────────────────

from dataclasses import dataclass


@dataclass
class ResultadoPrecoSimples:
    """Resultado simplificado para compatibilidade com código existente."""
    preco_final:        float
    custo_detectado:    float
    tax_rate_detectado: float
    comissao_usada:     float
    margem_usada:       float
    frete_usado:        float
    piso_aplicado:      bool


@dataclass
class ResultadoAnalise:
    preco_sugerido:    float
    status:            StatusMargem
    estrategia:        str
    margem_real:       float
    preco_concorrente: float
