"""
Métricas e alertas para o sistema de alocação de SKUs.
Retries, contagem de reciclados, falhas de sync.
"""
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("gerador_kits.metrics")


def log_sku_retry(sku: str, tentativa: int, motivo: str = "SKU already exists") -> None:
    logger.warning(
        "sku_retry sku=%s tentativa=%d motivo=%s",
        sku,
        tentativa,
        motivo,
        extra={"sku": sku, "tentativa": tentativa},
    )


def log_recycled_count(count: int, fonte: str = "sync") -> None:
    logger.info(
        "recycled_count count=%d fonte=%s",
        count,
        fonte,
        extra={"recycled_count": count, "fonte": fonte},
    )


def log_sync_falha(erro: str, detalhe: Optional[str] = None) -> None:
    logger.error(
        "sync_falha erro=%s detalhe=%s",
        erro,
        detalhe or "",
        extra={"erro": erro},
        exc_info=bool(detalhe),
    )


def log_alocacao_sucesso(sku: str, fonte: str = "sequencia") -> None:
    logger.info(
        "alocacao_sucesso sku=%s fonte=%s",
        sku,
        fonte,
        extra={"sku": sku, "fonte": fonte},
    )
