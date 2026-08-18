"""
criacao_estruturado/services/ficha_tecnica/__init__.py
Módulo de Ficha Técnica Inteligente com Rastreabilidade de Evidências,
Validação Determinística Python e Auditoria 100% por IA (Gemini).
"""
from .auditor import FichaTecnicaAuditor
from .categoria_atributos_service import CategoriaAtributosMLService
from .conflict_detector import ConflictDetector
from .evidence import EvidenceCollector
from .extractor import FichaTecnicaExtractor
from .ml_fill_service import MLAttributeFillService
from .ml_prompt_service import MLPromptService
from .models import AttributeCandidate, AttributeStatus, AuditReport, Evidence, EvidencePackage, SourceType
from .normalizer import FichaTecnicaNormalizer
from .orchestrator import FichaTecnicaOrchestrator
from .schemas import SchemaGenerator
from .validator import FichaTecnicaValidator

__all__ = [
    "FichaTecnicaOrchestrator",
    "EvidenceCollector",
    "FichaTecnicaExtractor",
    "FichaTecnicaNormalizer",
    "FichaTecnicaValidator",
    "ConflictDetector",
    "FichaTecnicaAuditor",
    "CategoriaAtributosMLService",
    "MLPromptService",
    "MLAttributeFillService",
    "SchemaGenerator",
    "EvidencePackage",
    "Evidence",
    "AttributeCandidate",
    "AttributeStatus",
    "SourceType",
    "AuditReport",
]
