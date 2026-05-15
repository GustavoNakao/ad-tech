"""
anuncios/services/processador_fila.py

Processa os itens da FilaAnuncio que ainda estão com status_margem=PENDENTE.
Chama a Calculadora (precificacao) e preenche:
    - preco_sugerido
    - margem_real
    - estrategia_gerada
    - status_margem (verde / amarelo / vermelho)
    - processado_em
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from precificacao.engine import StatusMargem
from precificacao.services.calculadora import Calculadora

logger = logging.getLogger("anuncios.processador")


class ProcessadorFila:
    """
    Uso:
        from anuncios.services.processador_fila import ProcessadorFila

        # Processar todos os pendentes
        resultado = ProcessadorFila().processar_pendentes()

        # Processar um item específico
        resultado = ProcessadorFila().processar_item(fila_anuncio)
    """

    def processar_pendentes(self) -> dict:
        """
        Busca todos os FilaAnuncio com status_margem=PENDENTE e processa.
        Retorna resumo com contagens.
        """
        from anuncios.models import FilaAnuncio

        pendentes = FilaAnuncio.objects.filter(
            status_margem=FilaAnuncio.StatusMargem.PENDENTE
        )

        total     = pendentes.count()
        verde     = 0
        amarelo   = 0
        vermelho  = 0
        erros     = []

        logger.info(f"Processando {total} item(s) pendente(s)...")

        for item in pendentes:
            try:
                self.processar_item(item)
                if item.status_margem == FilaAnuncio.StatusMargem.VERDE:
                    verde += 1
                elif item.status_margem == FilaAnuncio.StatusMargem.AMARELO:
                    amarelo += 1
                else:
                    vermelho += 1
            except Exception as exc:
                logger.error(f"Erro ao processar item id={item.pk}: {exc}")
                erros.append({"id": item.pk, "sku": item.sku_principal, "erro": str(exc)})

        logger.info(
            f"Concluído: {verde} verde(s), {amarelo} amarelo(s), "
            f"{vermelho} vermelho(s), {len(erros)} erro(s)."
        )

        return {
            "total":    total,
            "verde":    verde,
            "amarelo":  amarelo,
            "vermelho": vermelho,
            "erros":    erros,
        }

    def processar_item(self, item) -> None:
        """
        Processa um único FilaAnuncio.
        Extrai categoria da URL do ML, monta os dados, chama a Calculadora e salva o resultado.
        """
        from anuncios.models import FilaAnuncio
        import re

        # --- O SEU MOTOR DE PREÇO ORIGINAL INTACTO ---
        dados, sku_ref = self._montar_dados(item)

        preco_conc = float(item.preco_concorrente) if item.preco_concorrente else None

        if preco_conc:
            analise = Calculadora.analisar(
                dados,
                preco_concorrente=preco_conc,
                sku_ref=sku_ref,
            )
            item.preco_sugerido    = analise.preco_sugerido
            item.margem_real       = analise.margem_real
            item.estrategia_gerada = analise.estrategia
            item.status_margem     = self._mapear_status(analise.status)
        else:
            resultado = Calculadora.calcular(dados, sku_ref=sku_ref)
            item.preco_sugerido    = resultado.preco_final
            item.margem_real       = None
            item.estrategia_gerada = (
                "🟡 Sem preço do concorrente. Preço calculado com base no custo. "
                "Informe o concorrente para análise completa."
            )
            item.status_margem = FilaAnuncio.StatusMargem.AMARELO

        item.processado_em = datetime.now(tz=timezone.utc)
        
        item.save(update_fields=[
            "preco_sugerido", "margem_real", "estrategia_gerada",
            "status_margem", "processado_em"
        ])

        logger.info(
            f"[{sku_ref}] → {item.status_margem} | "
            f"R${item.preco_sugerido:.2f} | margem={item.margem_real}"
        )

    # ── Montagem de dados ──────────────────────────────────────────────────────

    def _montar_dados(self, item) -> tuple[dict, str]:
        """
        Monta o dict de dados que a Calculadora espera.
        Para UNIT/MULTI: usa o produto principal.
        Para MISTO: soma os custos dos dois produtos.
        """
        from anuncios.models import FilaAnuncio

        if item.tipo == FilaAnuncio.TipoAnuncio.MISTO and item.sku_secundario:
            return self._montar_dados_misto(item)

        # UNIT ou MULTI
        dados_prod = self._buscar_dados_produto(item.sku_principal)
        custo_unit = self._extrair_custo(dados_prod)
        peso_unit  = dados_prod.get("weight") or dados_prod.get("peso_kg") or 0.0

        qtd = item.qtd_principal or 1

        dados = dict(dados_prod)
        dados["average_cost"] = custo_unit * qtd
        dados["weight"]       = float(peso_unit) * qtd

        sku_ref = (
            item.sku_principal if qtd == 1
            else f"{item.sku_principal}_x{qtd}"
        )
        return dados, sku_ref

    def _montar_dados_misto(self, item) -> tuple[dict, str]:
        """Monta dados para kit MISTO somando custo e peso dos dois produtos."""
        dados_1 = self._buscar_dados_produto(item.sku_principal)
        dados_2 = self._buscar_dados_produto(item.sku_secundario)

        custo_1 = self._extrair_custo(dados_1) * (item.qtd_principal or 1)
        custo_2 = self._extrair_custo(dados_2) * (item.qtd_secundario or 1)

        peso_1 = float(dados_1.get("weight") or dados_1.get("peso_kg") or 0) * (item.qtd_principal or 1)
        peso_2 = float(dados_2.get("weight") or dados_2.get("peso_kg") or 0) * (item.qtd_secundario or 1)

        dados = {
            "average_cost": custo_1 + custo_2,
            "weight":       peso_1 + peso_2,
        }

        sku_ref = f"{item.sku_principal}+{item.sku_secundario}"
        return dados, sku_ref

    def _buscar_dados_produto(self, sku: str) -> dict:
        """
        Tenta buscar os dados do produto:
        1. Banco local (Produto.dados_raw_baselinker ou campos do model)
        2. Fallback: retorna dict vazio com custo 0
        """
        try:
            from produtos.models import Produto
            produto = Produto.objects.filter(sku=sku).first()
            if produto:
                return produto.dados_para_precificacao
        except Exception as exc:
            logger.warning(f"Produto {sku} não encontrado no banco: {exc}")

        logger.warning(f"SKU {sku} sem dados — usando custo zero.")
        return {"average_cost": 0.0}

    @staticmethod
    def _extrair_custo(dados: dict) -> float:
        for campo in ("average_cost", "custo_bruto_nf", "price_brutto", "price"):
            v = dados.get(campo)
            if v:
                try:
                    return float(str(v).replace(",", "."))
                except (ValueError, TypeError):
                    pass
        for chave, valor in dados.items():
            if isinstance(chave, str) and chave.startswith("prices_"):
                try:
                    f = float(str(valor).replace(",", "."))
                    if f > 0:
                        return f
                except (ValueError, TypeError):
                    pass
        return 0.0

    @staticmethod
    def _mapear_status(status_engine: StatusMargem) -> str:
        from anuncios.models import FilaAnuncio
        mapa = {
            StatusMargem.VERDE:    FilaAnuncio.StatusMargem.VERDE,
            StatusMargem.AMARELO:  FilaAnuncio.StatusMargem.AMARELO,
            StatusMargem.VERMELHO: FilaAnuncio.StatusMargem.VERMELHO,
        }
        return mapa.get(status_engine, FilaAnuncio.StatusMargem.AMARELO)
