"""
criacao_estruturado/services/ficha_tecnica/orchestrator.py

Orquestrador Central do Sistema de Ficha Técnica Inteligente.
Unifica o pipeline de 9 passos:
Encontrar → Extrair → Comprovar → Normalizar → Validar → Auditar → Corrigir/Bloquear → Validar Novamente → Publicar.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from integracoes.mercadolivre.client import MercadoLivreClient
from .auditor import FichaTecnicaAuditor
from .conflict_detector import ConflictDetector
from .evidence import EvidenceCollector
from .extractor import FichaTecnicaExtractor
from .models import AttributeCandidate, AttributeStatus, AuditReport, EvidencePackage
from .normalizer import FichaTecnicaNormalizer
from .validator import FichaTecnicaValidator

logger = logging.getLogger(__name__)


class FichaTecnicaOrchestrator:
    """
    Orquestrador Principal do Pipeline de Ficha Técnica Inteligente.
    """

    def __init__(self) -> None:
        self.ml_client = MercadoLivreClient()
        self.evidence_collector = EvidenceCollector()
        self.extractor = FichaTecnicaExtractor()
        self.normalizer = FichaTecnicaNormalizer()
        self.validator = FichaTecnicaValidator()
        self.conflict_detector = ConflictDetector()
        self.auditor = FichaTecnicaAuditor()

    def gerar_e_auditar_ficha_tecnica(
        self,
        contexto: str,
        atributos_obrigatorios: List[str],
        ean: str = "",
        marca: str = "",
        category_id_ml: str = "",
        produto_id: str = "0",
        sku: str = "",
        dados_internos: Optional[Dict[str, Any]] = None,
        quantidade: int = 1,
        pular_auditoria_llm: bool = False
    ) -> Tuple[Dict[str, str], AuditReport]:
        """
        Executa o pipeline completo de 9 passos.
        Retorna (features_finais_para_baselinker, relatorio_auditoria).
        """
        dados_internos = dados_internos or {}
        motivos_bloqueio: List[str] = []

        # Extrai o nome do produto a partir do contexto
        nome_produto = ""
        for linha in contexto.splitlines():
            if linha.strip().lower().startswith("produto:"):
                nome_produto = linha.split(":", 1)[-1].strip()
                break

        # ── PASSO 1 & 2: RESOLUÇÃO DA CATEGORIA DO USUÁRIO ──
        if category_id_ml:
            category_id_ml = self.ml_client.resolver_categoria_por_input(category_id_ml)
        else:
            category_id_ml = "MLB271283"

        logger.info("[FichaTecnicaOrchestrator] Iniciando pipeline para SKU '%s' (Cat: %s)...", sku or "N/A", category_id_ml)

        # ── PASSO 3: COLETA DE FONTES & PACOTE DE EVIDÊNCIAS ──
        package: EvidencePackage = self.evidence_collector.coletar_pacote_evidencias(
            produto_id=produto_id,
            sku=sku,
            category_id=category_id_ml,
            nome_produto=nome_produto,
            descricao_produto=contexto,
            ean=ean,
            marca=marca,
            atributos_obrigatorios=atributos_obrigatorios,
            dados_internos=dados_internos
        )

        # ── PASSO 4: EXTRAÇÃO DE CANDIDATOS VIA CATÁLOGO ML (CAMADA 1) + GROQ (CAMADA 2) ──
        features_candidatas: Dict[str, str] = {}

        # Resgata atributos do catálogo ML coletados no EvidencePackage
        for k, list_ev in package.evidencias_coletadas.items():
            for ev in list_ev:
                if ev.fonte.value == "catalogo_ml":
                    features_candidatas[k] = ev.valor

        # Identifica campos ainda ausentes para o Agente 4 (Groq)
        campos_ausentes = [
            c.split(" [")[0].strip() for c in atributos_obrigatorios
            if features_candidatas.get(c.split(" [")[0].strip()) is None
        ]

        if campos_ausentes:
            logger.info("[FichaTecnicaOrchestrator] Acionando Groq para %d campos ausentes...", len(campos_ausentes))
            atribs_groq = self.extractor.extrair_atributos_candidatos(package, campos_ausentes)
            for k, v in atribs_groq.items():
                if k not in features_candidatas and v:
                    features_candidatas[k] = v

        # ── PASSO 5: NORMALIZAÇÃO & EXPURGO CONTRA CHAVES PERMITIDAS ──
        features_normalizadas = self.normalizer.normalizar_e_expurgar(features_candidatas, atributos_obrigatorios)

        # ── PASSO 6: VALIDAÇÃO DETERMINÍSTICA 1 (PYTHON) & KITS ──
        features_kits = self.validator.resolver_atributos_fisicos_kit(features_normalizadas, quantidade=quantidade)

        # ── PASSO 7: DETECÇÃO DE CONFLITOS E HIERARQUIA DE FONTES ──
        features_sem_conflitos, atributos_detalhados, conflitos = self.conflict_detector.analisar_e_resolver_conflitos(
            features_kits, package
        )

        # ── PASSO 8: AUDITORIA FACTUAL EM 100% DAS FICHAS VIA GEMINI (AGENTE 7) ──
        if pular_auditoria_llm:
            res_auditoria_gemini = {"status_geral": "APROVADO", "atributos_auditados": []}
        else:
            res_auditoria_gemini = self.auditor.auditar_ficha_tecnica(package, features_sem_conflitos, atributos_detalhados)

        # Atualiza a lista de atributos detalhados com as respostas do Gemini
        for item_audit in res_auditoria_gemini.get("atributos_auditados", []):
            nome_attr = item_audit.get("atributo")
            st_audit = item_audit.get("status")
            val_rec = item_audit.get("valor_recomendado")
            just = item_audit.get("justificativa", "")

            # Atualiza o estado correspondente
            for cand in atributos_detalhados:
                if cand.nome.lower() == str(nome_attr).lower():
                    if st_audit == "CONFIRMADO":
                        cand.status = AttributeStatus.CONFIRMADO
                    elif st_audit == "CONTRADITO":
                        cand.status = AttributeStatus.CONTRADITO
                        if val_rec and str(val_rec).strip():
                            cand.valor_normalizado = str(val_rec).strip()
                            features_sem_conflitos[cand.nome] = str(val_rec).strip()
                            logger.info("[FichaTecnicaOrchestrator] Correção pela auditoria no atributo '%s' -> '%s' (%s)", cand.nome, val_rec, just)
                    elif st_audit in ("NAO_VERIFICADO", "INVALIDO"):
                        cand.status = AttributeStatus.NAO_VERIFICADO

        # ── PASSO 9: REGRA DE OMISSÃO ESTRITA (ZERO INCERTEZA) & DECISÃO DE PUBLICAÇÃO ──
        # Se um atributo não for 100% verificado ou for suspeito/inválido, ele é omitido.
        features_estritas = dict(features_sem_conflitos)
        for cand in atributos_detalhados:
            if cand.status in (AttributeStatus.NAO_VERIFICADO, AttributeStatus.INVALIDO):
                # Mantém apenas atributos estruturais fixos de kit/unidade se foram calculados
                if cand.nome not in ("Formato de venda", "Quantidade", "Unidades por kit", "Condição do item", "Tipo de unidade", "Unidade de venda"):
                    if cand.nome in features_estritas:
                        logger.info("[FichaTecnicaOrchestrator] Omitindo atributo não 100%% verificado: '%s'", cand.nome)
                        del features_estritas[cand.nome]

        valido_final, erros_finais = self.validator.validar_ficha_tecnica(features_estritas, atributos_obrigatorios)

        bloqueado = False
        if not valido_final:
            for err in erros_finais:
                motivos_bloqueio.append(err)
            bloqueado = True

        status_geral = "BLOQUEADO" if bloqueado else "APROVADO"

        relatorio = AuditReport(
            status_geral=status_geral,
            atributos_finais=features_estritas,
            atributos_detalhados=atributos_detalhados,
            conflitos_detectados=conflitos,
            bloqueado=bloqueado,
            motivos_bloqueio=motivos_bloqueio,
            score_confiabilidade=95.0 if not bloqueado else 40.0
        )

        logger.info("[FichaTecnicaOrchestrator] Pipeline concluído para SKU '%s' | Status Geral: %s | Atributos Finais: %d.",
                    sku or "N/A", status_geral, len(features_estritas))

        # Persiste histórico auditável no PostgreSQL de forma resiliente
        self._persisti_relatorio_no_banco(sku, produto_id, category_id_ml, relatorio)

        return features_estritas, relatorio

    def _persisti_relatorio_no_banco(
        self,
        sku: str,
        id_kit: str,
        category_id: str,
        relatorio: AuditReport
    ) -> None:
        """
        Salva a trilha de auditoria completa nos modelos Django do PostgreSQL.
        """
        try:
            from criacao_estruturado.models import (
                FichaExecucao, FichaAtributo, FichaEvidencia, FichaAuditoria, FichaConflito
            )
            execucao = FichaExecucao.objects.create(
                sku=sku or "N/A",
                id_kit=str(id_kit or ""),
                category_id=category_id or "",
                modelo_extrator=self.extractor.MODELO_PRIMARIO,
                modelo_auditor="gemini-2.5-flash",
                status=relatorio.status_geral,
                score_confiabilidade=relatorio.score_confiabilidade
            )

            for cand in relatorio.atributos_detalhados:
                atrib_db = FichaAtributo.objects.create(
                    execucao=execucao,
                    nome=cand.nome,
                    valor_original=cand.valor_original,
                    valor_normalizado=cand.valor_normalizado,
                    status=cand.status.value if hasattr(cand.status, "value") else str(cand.status),
                    fonte_principal=cand.fonte_principal.value if hasattr(cand.fonte_principal, "value") else str(cand.fonte_principal)
                )

                for ev in cand.evidencias:
                    FichaEvidencia.objects.create(
                        atributo=atrib_db,
                        fonte=ev.fonte.value if hasattr(ev.fonte, "value") else str(ev.fonte),
                        valor=ev.valor,
                        trecho=ev.trecho,
                        prioridade=ev.prioridade
                    )

                FichaAuditoria.objects.create(
                    atributo=atrib_db,
                    modelo="gemini-2.5-flash",
                    status_auditoria=cand.status.value if hasattr(cand.status, "value") else str(cand.status),
                    valor_analisado=cand.valor_original,
                    valor_recomendado=cand.valor_normalizado,
                    justificativa="Auditado via Agente 7 Gemini"
                )

            for conf in relatorio.conflitos_detectados:
                FichaConflito.objects.create(
                    execucao=execucao,
                    atributo_nome=conf.get("atributo", ""),
                    fonte_a="IA Extratora",
                    valor_a=str(conf.get("fonte_ia", "")),
                    fonte_b=str(conf.get("fonte_superior", "")),
                    valor_b=str(conf.get("valor_superior", "")),
                    resolucao=str(conf.get("resolucao", ""))
                )
            logger.info("[FichaTecnicaOrchestrator] ✅ Trilha de auditoria salva com sucesso em FichaExecucao (ID %d).", execucao.id)

        except Exception as e_db:
            logger.warning("[FichaTecnicaOrchestrator] Aviso: Falha ao salvar trilha no banco local: %s", e_db)

