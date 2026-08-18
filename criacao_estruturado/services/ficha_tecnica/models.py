"""
criacao_estruturado/services/ficha_tecnica/models.py

Dataclasses e Enums para representação de estados de atributos,
evidências, pacotes de contextos e relatórios de auditoria.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class AttributeStatus(str, Enum):
    CONFIRMADO = "CONFIRMADO"       # Comprovado por evidência direta confiável
    CONTRADITO = "CONTRADITO"       # Divergência encontrada com fonte de maior prioridade
    NAO_VERIFICADO = "NAO_VERIFICADO"# Sem evidência no contexto do produto
    INFERIDO = "INFERIDO"           # Derivado por lógica física matemática (ex: volume/peso do kit)
    INVALIDO = "INVALIDO"           # Fora dos limites/enums/especificações da categoria
    REVISAR = "REVISAR"             # Requer atenção manual


class SourceType(str, Enum):
    CATALOGO_ML = "catalogo_ml"             # Prioridade 100
    FABRICANTE = "fabricante"               # Prioridade 95
    BANCO_INTERNO = "banco_interno"         # Prioridade 90
    DADOS_PRODUTO = "dados_produto"         # Prioridade 75
    DESCRICAO_SEO = "descricao_seo"         # Prioridade 50
    IA_EXTRATORA = "ia_extratora"           # Prioridade 40


@dataclass
class Evidence:
    fonte: SourceType
    valor: str
    trecho: str = ""
    prioridade: int = 50
    confianca: float = 1.0


@dataclass
class AttributeCandidate:
    nome: str
    valor_original: Optional[str] = None
    valor_normalizado: Optional[str] = None
    status: AttributeStatus = AttributeStatus.NAO_VERIFICADO
    fonte_principal: SourceType = SourceType.IA_EXTRATORA
    evidencias: List[Evidence] = field(default_factory=list)
    conflitos: List[str] = field(default_factory=list)


@dataclass
class EvidencePackage:
    produto_id: str
    sku: str
    category_id: str
    nome_produto: str
    descricao_produto: str = ""
    ean: str = ""
    marca: str = ""
    atributos_obrigatorios: List[str] = field(default_factory=list)
    chaves_permitidas: List[str] = field(default_factory=list)
    evidencias_coletadas: Dict[str, List[Evidence]] = field(default_factory=dict)
    dados_internos: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AuditReport:
    status_geral: str  # "APROVADO", "BLOQUEADO", "REVISAR"
    atributos_finais: Dict[str, str] = field(default_factory=dict)
    atributos_detalhados: List[AttributeCandidate] = field(default_factory=list)
    conflitos_detectados: List[Dict[str, Any]] = field(default_factory=list)
    bloqueado: bool = False
    motivos_bloqueio: List[str] = field(default_factory=list)
    score_confiabilidade: float = 100.0
