"""
precificacao/engine.py — Motor de Precificação R831 v2.0

Implementa as regras do documento Motor_Precificacao_R831_v2:
    - Peso cubado vs peso real (MAX)
    - Desconto de frete por reputação (50% para Verde)
    - Tabela oficial Mercado Envios
    - Custos operacionais (BaseLinker R$0,79 + GNRE R$1,50)
    - Trava Fiscal TEF/DIFAL 4,5%
    - Tarifa Reduzida (R$150–R$700)
    - Preço de Vitrine = Preço de Promoção / 0,80
    - Teto de Vitrine (Markup Máximo 60%)
    - Regra de Truncamento em Cascata (SKILL.md)
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("precificacao.engine")

# ── Tax Rate ENUM válidos ─────────────────────────────────────────────────────
TAX_RATES_VALIDOS = {0.0, 9.25, 27.25}

# ── Aliases para detectar custo nos campos do produto ────────────────────────
_CUSTO_CAMPOS = ("average_cost", "price_brutto", "price", "custo_bruto_nf")
_TAX_ALIASES  = ("tax_rate", "vat", "aliquota", "icms", "imposto", "tax", "iva")


class StatusMargem(str, Enum):
    VERDE    = "verde"
    AMARELO  = "amarelo"
    VERMELHO = "vermelho"


class ModalidadeAnuncio(str, Enum):
    CLASSICO = "classico"
    PREMIUM  = "premium"


@dataclass
class ResultadoAnuncio:
    """Resultado de um anúncio individual (1 das 8 combinações)."""
    referencia:               str
    quantidade:               int
    modalidade:               str
    preco_promocao:           float
    preco_vitrine:            float
    custo_logistico:          float
    denominador:              float
    numerador:                float
    tarifa_reduzida_aplicada: bool
    comissao_usada:           float
    tax_rate_usado:           float
    peso_efetivo:             float
    erros:                    List[str] = field(default_factory=list)
    bloqueado:                bool = False
    audit_tag:                str = ""

    def to_dict(self) -> dict:
        """Converte o resultado para formato aceito pelo BaseLinker/Views."""
        return {
            "sku":                     "",
            "referencia":              self.referencia,
            "kit_quantity":            self.quantidade,
            "listing_type":            "gold_special" if self.modalidade == "premium" else "bronze",
            "price_floor":             self.preco_promocao,
            "price_hub":               self.preco_vitrine,
            "net_margin_pct":          0.12,
            "tarifa_reduzida_applied": self.tarifa_reduzida_aplicada,
            "logistics_cost":          self.custo_logistico,
            "denominador":             round(self.denominador, 6),
            "numerador":               round(self.numerador, 4),
            "comissao_usada":          self.comissao_usada,
            "tax_rate_usado":          self.tax_rate_usado,
            "peso_efetivo_kg":         self.peso_efetivo,
            "bloqueado":               self.bloqueado,
            "erros":                   self.erros,
            "audit_tag":               self.audit_tag,
        }


@dataclass
class ResultadoGrade:
    """Grade completa de 8 anúncios para um SKU."""
    sku:       str
    anuncios:  List[ResultadoAnuncio]
    bloqueado: bool = False
    motivo_bloqueio: str = ""
    audit_tag: str = ""

    @property
    def anuncios_validos(self) -> List[ResultadoAnuncio]:
        return [a for a in self.anuncios if not a.bloqueado]

    def log_resumo(self) -> str:
        """Gera log amigável da grade."""
        linhas = [f"=== Grade R831 — SKU: {self.sku} ==="]
        if self.bloqueado:
            linhas.append(f"🔴 BLOQUEADO: {self.motivo_bloqueio}")
            return "\n".join(linhas)
        for a in self.anuncios:
            status = "🔴 BLOQUEADO" if a.bloqueado else "✅"
            linhas.append(f"  {status} {a.referencia:<22} | Promoção: R$ {a.preco_promocao:>7.2f} | Vitrine: R$ {a.preco_vitrine:>7.2f} {a.audit_tag}")
        return "\n".join(linhas)


@dataclass
class ConfigEngine:
    """Configuração para o motor de precificação."""
    margem_minima:             float = 0.12
    trava_fiscal:              float = 0.045
    comissao_classico:         float = 0.115
    comissao_premium:          float = 0.165
    comissao_classico_reduzida: float = 0.095
    comissao_premium_reduzida:  float = 0.14
    tarifa_reduzida_min:       float = 150.01
    tarifa_reduzida_max:       float = 700.00
    custo_baselinker:          float = 0.79
    custo_gnre:                float = 1.50
    piso_frete_gratis:         float = 79.00
    taxa_fixa_ml:              float = 6.50
    piso_faixa_min:            float = 19.00
    piso_faixa_max:            float = 78.99
    piso_valor_forcado:        float = 79.90
    desconto_vitrine:          float = 0.20     # desconto de 20% para selo de oferta
    margem_maxima_vitrine:     float = 0.60     # teto de 60% markup sobre custo
    peso_max_mercado_envios:   float = 30.00    # limite para Mercado Envios (kg)
    terminacao_preco:          float = 0.90     # ex: 49.90
    reputacao_conta:           str   = "verde"  # "verde" = 50% desconto frete
    elegivel_tarifa_reduzida:  bool  = False    # definido na CategoriaComissao
    protocolo_greenfield:      bool  = False    # Motor v4.0
    vendas_60_dias:            int   = 0        # Regra Patrimonio vs Greenfield

    @classmethod
    def from_regras(cls, regras: Any, categoria_comissao: Any = None) -> ConfigEngine:
        """Cria config a partir dos modelos RegrasPrecificacao e CategoriaComissao."""
        cfg = cls(
            margem_minima              = float(regras.margem_minima),
            trava_fiscal               = float(regras.trava_fiscal),
            comissao_classico          = float(regras.comissao_classico_fallback),
            comissao_premium           = float(regras.comissao_premium_fallback),
            comissao_classico_reduzida = float(regras.comissao_classico_reduzida),
            comissao_premium_reduzida  = float(regras.comissao_premium_reduzida),
            tarifa_reduzida_min        = float(regras.tarifa_reduzida_min),
            tarifa_reduzida_max        = float(regras.tarifa_reduzida_max),
            custo_baselinker           = float(regras.custo_baselinker),
            custo_gnre                 = float(regras.custo_gnre),
            piso_frete_gratis          = float(regras.piso_frete_gratis),
            taxa_fixa_ml               = float(regras.taxa_fixa_ml),
            piso_faixa_min             = float(regras.piso_faixa_min),
            piso_faixa_max             = float(regras.piso_faixa_max),
            piso_valor_forcado         = float(regras.piso_valor_forcado),
            desconto_vitrine           = float(regras.desconto_vitrine),
            margem_maxima_vitrine      = float(regras.margem_maxima_vitrine),
            peso_max_mercado_envios    = float(regras.peso_max_mercado_envios),
            terminacao_preco           = float(regras.terminacao_preco),
            reputacao_conta            = regras.reputacao_conta,
            protocolo_greenfield       = getattr(regras, 'protocolo_greenfield', False),
        )
        if categoria_comissao:
            cfg.comissao_classico         = float(categoria_comissao.comissao_classico)
            cfg.comissao_premium          = float(categoria_comissao.comissao_premium)
            cfg.elegivel_tarifa_reduzida  = categoria_comissao.elegivel_tarifa_reduzida
        return cfg


class PricingEngine:
    """Motor de precificação R831 v2.0 refatorado."""

    def __init__(self, config: Optional[ConfigEngine] = None) -> None:
        self.config = config or ConfigEngine()

    def calcular_grade(
        self, dados: Dict[str, Any], sku_ref: str = "???", custo_saida_transportadora: Optional[float] = None, vendas_60_dias: int = 0
    ) -> ResultadoGrade:
        """Calcula a grade de 8 anúncios para um SKU."""
        # Sobrescreve vendas na config se passado
        self.config.vendas_60_dias = vendas_60_dias
        
        custo = self._extrair_custo(dados)
        peso = self._calcular_peso_efetivo(dados)
        tax, tax_erros = self._extrair_tax_rate_validado(dados)

        bloq = self._validar_entrada(custo, peso, tax_erros, custo_saida_transportadora, sku_ref)
        if bloq:
            return ResultadoGrade(sku=sku_ref, anuncios=[], bloqueado=True, motivo_bloqueio=bloq)

        anuncios = self._gerar_anuncios(custo, tax, peso, sku_ref, custo_saida_transportadora)
        return ResultadoGrade(sku=sku_ref, anuncios=anuncios)

    def _validar_entrada(self, c: float, p: float, te: List[str], ct: Optional[float], s: str) -> Optional[str]:
        """Verifica se o produto pode ser precificado."""
        if c <= 0: return f"Custo zero para SKU={s}"
        if p > self.config.peso_max_mercado_envios and not ct:
            return f"Peso {p:.2f}kg acima do limite sem transportadora"
        if te: return f"Erros no tax_rate: {te}"
        return None

    def _gerar_anuncios(self, c: float, t: float, p: float, s: str, ct: Optional[float]) -> List[ResultadoAnuncio]:
        """Loop de geração dos 8 anúncios."""
        res = []
        combs = [
            (1, ModalidadeAnuncio.CLASSICO, "unit_1_classico"), (1, ModalidadeAnuncio.PREMIUM, "unit_1_premium"),
            (2, ModalidadeAnuncio.CLASSICO, "unit_2_classico"), (2, ModalidadeAnuncio.PREMIUM, "unit_2_premium"),
            (3, ModalidadeAnuncio.CLASSICO, "unit_3_classico"), (3, ModalidadeAnuncio.PREMIUM, "unit_3_premium"),
            (4, ModalidadeAnuncio.CLASSICO, "unit_4_classico"), (4, ModalidadeAnuncio.PREMIUM, "unit_4_premium"),
        ]
        for q, m, r in combs:
            res.append(self._calcular_anuncio(c, t, p, q, m, r, s, ct))
            
        # --- Garantia de Monotonicidade (Kits maiores não podem ser mais baratos) ---
        # No Greenfield, permitimos que kits com mais unidades sejam mais baratos (ex: Kit 2 vs 1) 
        # para forçar a venda do Kit 2 (produto estrela) devido à isenção da taxa fixa.
        if not self.config.protocolo_greenfield:
            # Ordena por modalidade para comparar 1un C com 2un C, etc.
            for mod in [ModalidadeAnuncio.CLASSICO.value, ModalidadeAnuncio.PREMIUM.value]:
                anuncios_mod = [a for a in res if a.modalidade == mod]
                anuncios_mod.sort(key=lambda x: x.quantidade)
                
                ultimo_p_vit = 0.0
                for a in anuncios_mod:
                    if a.preco_vitrine < ultimo_p_vit:
                        a.preco_vitrine = ultimo_p_vit
                        a.preco_promocao = _arredondar_teto(a.preco_vitrine * (1 - self.config.desconto_vitrine))
                        a.audit_tag += " - AJUSTE_MONOTONICO"
                    ultimo_p_vit = a.preco_vitrine
                
        return res

    def _calcular_anuncio(
        self, custo: float, tax: float, peso: float, qtd: int, 
        mod: ModalidadeAnuncio, ref: str, sku: str, trans: Optional[float]
    ) -> ResultadoAnuncio:
        """Calcula um anúncio unitário ou kit."""
        cfg = self.config
        is_p = mod == ModalidadeAnuncio.PREMIUM
        
        # --- Protocolo de Sincronização: Versão 4.3 (Greenfield) ---
        # Ativa se marcado manualmente OU se vendas < 30
        is_greenfield = cfg.protocolo_greenfield or cfg.vendas_60_dias < 30
        
        if is_greenfield:
            # Regra 1: Respeitar modalidade
            com = cfg.comissao_premium if is_p else cfg.comissao_classico 
            margem_meta = 0.25
            trava_fiscal = 0.0
            c_bl = 0.0
            c_gnre = 0.0
            if tax == 0.0:
                tax = 0.2725
                logger.info(f"Tax Rate ausente no BaseLinker, forçando 27.25% (Tax Guard Soberano) para SKU {sku}")
            audit_tag = "[R831-GREENFIELD]"
            logger.info(f"MODO GREENFIELD ATIVADO para SKU {sku} ({ref}) | Margem: {margem_meta} | Com: {com} | Tax: {tax}")
        else:
            com = cfg.comissao_premium if is_p else cfg.comissao_classico
            margem_meta = cfg.margem_minima
            trava_fiscal = cfg.trava_fiscal
            c_bl = cfg.custo_baselinker
            c_gnre = cfg.custo_gnre
            audit_tag = "[R831-PATRIMONIO]"
            logger.info(f"MODO PATRIMONIO ATIVADO para SKU {sku} ({ref}) | Margem: {margem_meta} | Com: {com}")
            
        den = margem_meta + tax + trava_fiscal + com
        
        if den >= 1: return self._bloqueio_denominador(ref, qtd, mod, den, com, tax, peso)

        c_kit = custo * qtd
        
        # Cálculo Inicial
        c_log = self._obter_frete_estimado(c_kit, den, peso, qtd, trans, c_bl, c_gnre)
        num = c_kit + c_log + c_bl + c_gnre
        prom = _arredondar_teto(num / (1 - den))
        vit = _arredondar_teto(prom / (1 - cfg.desconto_vitrine))

        # --- Regra 3: Implementação da Lógica de Hysteresis (O Salto dos R$ 79) ---
        if is_greenfield:
            # 1. Tenta Sem Taxa (Ticket >= 79)
            c_log_sem = self._calcular_custo_logistico(peso * qtd, 79.0, qtd, trans)
            num_sem = c_kit + c_log_sem + c_bl + c_gnre
            prom_sem = _arredondar_teto(num_sem / (1 - den))
            vit_sem = _arredondar_teto(prom_sem / (1 - cfg.desconto_vitrine))
            
            # 2. Tenta Com Taxa (Ticket < 79)
            c_log_com = self._calcular_custo_logistico(peso * qtd, 10.0, qtd, trans) # 10.0 força a taxa
            num_com = c_kit + c_log_com + c_bl + c_gnre
            prom_com = _arredondar_teto(num_com / (1 - den))
            vit_com = _arredondar_teto(prom_com / (1 - cfg.desconto_vitrine))
            
            # Lógica de decisão
            if vit_sem >= 79.00:
                # Se sem taxa já fica >= 79, usamos esse preço (naturalmente fora da zona morta)
                vit, prom, c_log, num = vit_sem, prom_sem, c_log_sem, num_sem
                logger.info("%s - Naturalmente >= 79 para SKU %s", audit_tag, sku)
            elif 72.00 <= vit_sem <= 78.99:
                # Caso clássico: o preço sem taxa cairia na zona morta se não houvesse a regra,
                # ou cairia abaixo de 79 e forçaria a taxa. Saltamos para 79.
                vit, prom, c_log, num = 79.00, _arredondar_teto(79.00 * (1 - cfg.desconto_vitrine)), c_log_sem, num_sem
                audit_tag += " - SALTO 79"
                logger.info("%s - Salto 79 (via vit_sem) para SKU %s", audit_tag, sku)
            else:
                # Caso contrário, usamos o preço com taxa (preço baixo < 72)
                vit, prom, c_log, num = vit_com, prom_com, c_log_com, num_com
                logger.info("%s - Preço Baixo (< 72) para SKU %s", audit_tag, sku)
        else:
            # Lógica Patrimônio Original
            c_log = self._obter_frete_estimado(c_kit, den, peso, qtd, trans, c_bl, c_gnre)
            num = c_kit + c_log + c_bl + c_gnre
            prom = _arredondar_teto(num / (1 - den))
            vit = _arredondar_teto(prom / (1 - cfg.desconto_vitrine))
            logger.info("%s - Calculado para SKU %s", audit_tag, sku)

        prom, com_final, den_final, tr = self._aplicar_tarifa_reduzida(prom, num, tax, is_p, margem_meta, trava_fiscal, com)
        
        # Cascata de truncamento (Teto de markup)
        # No Greenfield, o teto é ignorado para permitir preços de guerra agressivos.
        if is_greenfield:
            vit = _arredondar_teto(prom / (1 - cfg.desconto_vitrine))
            return ResultadoAnuncio(ref, qtd, mod.value, prom, vit, c_log, den_final, num, tr, com_final, tax, peso, [], False, audit_tag)
            
        vit, prom, err = self._aplicar_cascata(prom, c_kit, sku, ref, is_p)

        return ResultadoAnuncio(ref, qtd, mod.value, prom, vit, c_log, den_final, num, tr, com_final, tax, peso, err, False, audit_tag)

    def _obter_frete_estimado(self, ck: float, d: float, p: float, q: int, t: Optional[float], c_bl: float = 0, c_gnre: float = 0) -> float:
        """Estima o preço para decidir a faixa de frete (usa Vitrine)."""
        cfg = self.config
        # Para uma estimativa realista, incluímos o frete base no numerador
        c_tab = self._consultar_tabela_frete(p * q)
        frete_base = c_tab * 0.5 if cfg.reputacao_conta == "verde" else c_tab
        
        pg_est = (ck + frete_base + c_bl + c_gnre) / (1 - d)
        vit_est = _arredondar_teto(pg_est / (1 - cfg.desconto_vitrine))
        return self._calcular_custo_logistico(p * q, vit_est, q, t)

    def _aplicar_tarifa_reduzida(self, p: float, n: float, t: float, is_p: bool, m_meta: float = 0.12, t_fiscal: float = 0.045, com_atual: float = 0) -> Tuple[float, float, float, bool]:
        """Reduz comissão se preço entrar na faixa permitida."""
        cfg = self.config
        # Se não for passado com_atual, tenta pegar das configs padrão
        if com_atual == 0:
            com_atual = cfg.comissao_premium if is_p else cfg.comissao_classico

        if not cfg.elegivel_tarifa_reduzida or not (cfg.tarifa_reduzida_min <= p <= cfg.tarifa_reduzida_max):
            return p, com_atual, (m_meta + t + t_fiscal + com_atual), False
        
        com_r = cfg.comissao_premium_reduzida if is_p else cfg.comissao_classico_reduzida
        den_r = m_meta + t + t_fiscal + com_r
        p_r = max(cfg.tarifa_reduzida_min, _arredondar_teto(n / (1 - den_r)))
        return p_r, com_r, den_r, True

    def _aplicar_cascata(self, p_ideal: float, ck: float, s: str, r: str, is_p: bool = False) -> Tuple[float, float, List[str]]:
        """Garante Vitrine >= Promoção e aplica o Teto de 60% sobre a PROMOÇÃO."""
        cfg = self.config
        
        # O Teto limita o preço final de venda (Promoção)
        markup_max = cfg.margem_maxima_vitrine
        if is_p:
            # GAP Premium: Adiciona a diferença de comissão ao teto para permitir que o Premium seja mais caro que o Clássico
            # mesmo quando ambos atingem o teto de markup.
            markup_max += (cfg.comissao_premium - cfg.comissao_classico)

        teto_p = _arredondar_teto(ck * (1 + markup_max))
        
        p_f = p_ideal
        err = []
        if p_ideal > teto_p:
            p_f = teto_p
            err = ["MARGEM INSUFICIENTE"]
            
        # Vitrine calculada sobre o preço de venda final (p_f)
        vit = _arredondar_teto(p_f / (1 - cfg.desconto_vitrine))
        
        logger.info(
            "LOG CASCATA [%s]: Custo=%.2f | Teto_P=%.2f | Ideal=%.2f | Promo=%.2f | Vitrine=%.2f",
            s, ck, teto_p, p_ideal, p_f, vit
        )
        return vit, p_f, err

    def _calcular_peso_efetivo(self, d: Dict) -> float:
        """Calcula o maior entre peso real e cubado."""
        pr = _para_float(d.get("weight")) or 0.0
        h, w, l = _para_float(d.get("height")) or 0, _para_float(d.get("width")) or 0, _para_float(d.get("length")) or 0
        pc = (h * w * l) / 6000.0 if (h*w*l) > 0 else 0.0
        return max(pr, pc)

    def _calcular_custo_logistico(self, p: float, pe: float, q: int, t: Optional[float]) -> float:
        """Decide entre transportadora, taxa fixa ou tabela ME."""
        cfg = self.config
        if p > cfg.peso_max_mercado_envios: return t or 0.0
        
        c_tab = self._consultar_tabela_frete(p)
        frete_me = c_tab * 0.5 if cfg.reputacao_conta == "verde" else c_tab
        
        if pe < cfg.piso_frete_gratis:
            if cfg.protocolo_greenfield:
                # No Greenfield, o frete é diluído e a taxa é somada se < 79
                # A taxa é por anúncio (venda), não por unidade no kit
                return frete_me + cfg.taxa_fixa_ml
            return cfg.taxa_fixa_ml
            
        return frete_me

    def _consultar_tabela_frete(self, p: float) -> float:
        """Busca o custo na tabela oficial no banco de dados."""
        try:
            from precificacao.models.regras import TabelaFreteMercadoEnvios
            ent = TabelaFreteMercadoEnvios.objects.filter(peso_min_kg__lte=p, peso_max_kg__gte=p, ativo=True).first()
            return float(ent.custo_tabela) if ent else 18.0
        except: return 18.0

    def _extrair_custo(self, d: Dict) -> float:
        """Extrai o custo base do dicionário do BaseLinker."""
        for c in _CUSTO_CAMPOS:
            v = _para_float(d.get(c))
            if v and v > 0: return v
        cands = [_para_float(val) for k, val in d.items() if k.startswith("prices_") and _para_float(val)]
        return min(cands) if cands else 0.0

    def _extrair_tax_rate_validado(self, d: Dict) -> Tuple[float, List[str]]:
        """Extrai e valida se o tax_rate é 0, 9.25 ou 27.25."""
        raw = next((d.get(a) for a in _TAX_ALIASES if d.get(a) is not None), None)
        if raw is None: return 0.0, []
        try:
            v = float(str(raw).replace(",", ".").strip())
            v_dec = v / 100.0 if v > 1.0 else v
            if round(v_dec * 100, 2) not in TAX_RATES_VALIDOS:
                return 0.0, [f"Tax {v_dec*100}% inválido"]
            return v_dec, []
        except: return 0.0, ["Tax não numérico"]

    def _bloqueio_denominador(self, r, q, m, d, c, t, p) -> ResultadoAnuncio:
        """Gera objeto de erro quando denominador explode."""
        return ResultadoAnuncio(r, q, m.value, 0, 0, 0, d, 0, False, c, t, p, ["DENOMINADOR INVÁLIDO"], True)


def _para_float(v: Any) -> Optional[float]:
    """Helper robusto para converter strings e números para float."""
    if v is None or isinstance(v, bool): return None
    if isinstance(v, (int, float)): return float(v)
    try:
        s = str(v).replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
        return float(s)
    except: return None


def _arredondar_teto(v: float) -> float:
    """Arredonda sempre para cima na 2ª casa decimal."""
    return math.ceil(v * 100) / 100
