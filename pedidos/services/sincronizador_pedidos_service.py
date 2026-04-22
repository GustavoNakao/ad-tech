"""
SincronizadorPedidosService
============================
Paginacao incremental por date_confirmed_from, conforme recomendacao oficial BaseLinker.

Logica:
  1. Primeira requisicao com date_confirmed_from = timestamp inicial.
  2. API retorna ate 100 pedidos ordenados por date_confirmed.
  3. Captura o maior date_confirmed da pagina + 1 segundo como novo cursor.
  4. Repete ate receber menos de 100 itens ou lista vazia.

Uso:
  executar_sincronizacao(historico_completo=True)  -> tudo desde o inicio
  executar_sincronizacao(dias_atras=30)            -> ultimos N dias
"""

import logging
import time
from datetime import datetime
from typing import Dict, List, Optional

from django.db import transaction
from django.utils.timezone import make_aware

from baselinker_integracao.client import BaselinkerClient
from pedidos.models import StatusPedido, Pedido, ItemPedido

logger = logging.getLogger('sincronizador_pedidos')

TAMANHO_PAGINA = 100
SLEEP_ENTRE_PAGINAS = 0.3
SLEEP_RATE_LIMIT = 2.0
MAX_RETRIES = 3


class SincronizadorPedidosService:

    def __init__(self):
        self.client = BaselinkerClient()

    # -------------------------------------------------------------------------
    # Ponto de entrada publico
    # -------------------------------------------------------------------------

    def executar_sincronizacao(
        self,
        historico_completo: bool = False,
        dias_atras: int = 7,
    ) -> Dict:
        if historico_completo:
            timestamp_cursor = 1  # epoch = pega tudo desde o inicio da conta
            logger.info("Modo: historico completo (date_confirmed_from=1)")
        else:
            timestamp_cursor = int(time.time()) - (dias_atras * 86400)
            logger.info(f"Modo: ultimos {dias_atras} dias (date_confirmed_from={timestamp_cursor})")

        totais = {'processados': 0, 'criados': 0, 'atualizados': 0, 'ignorados': 0, 'erros': 0}
        pagina = 1

        while True:
            logger.info(f"Pagina {pagina} | cursor={timestamp_cursor}")

            params = {
                "date_confirmed_from": timestamp_cursor,
                "get_unconfirmed_orders": True,
            }

            try:
                resp = self._request_seguro("getOrders", params)
            except Exception as e:
                logger.error(f"Falha critica na pagina {pagina}: {e}")
                return {"sucesso": False, "msg": str(e), "totais": totais}

            orders: List[Dict] = resp.get("orders", [])

            if not orders:
                logger.info("Nenhum pedido retornado -- fim da paginacao.")
                break

            logger.info(f"{len(orders)} pedidos recebidos")

            for pedido_data in orders:
                resultado = self._processar_unico_pedido(pedido_data)
                totais['processados'] += 1
                if resultado == 'criado':
                    totais['criados'] += 1
                elif resultado == 'atualizado':
                    totais['atualizados'] += 1
                elif resultado == 'ignorado':
                    totais['ignorados'] += 1
                else:
                    totais['erros'] += 1

            if len(orders) < TAMANHO_PAGINA:
                logger.info("Menos de 100 itens -- ultima pagina, encerrando.")
                break

            # Avanca cursor: maior date_confirmed da pagina + 1 segundo
            maior_ts = max(
                o.get('date_confirmed') or o.get('date_add') or 0
                for o in orders
            )
            if maior_ts <= 0:
                logger.warning("Cursor invalido -- encerrando.")
                break

            timestamp_cursor = maior_ts + 1
            pagina += 1
            time.sleep(SLEEP_ENTRE_PAGINAS)

        msg = (
            f"Sincronizacao finalizada! "
            f"{totais['processados']} processados -- "
            f"{totais['criados']} criados, "
            f"{totais['atualizados']} atualizados, "
            f"{totais['ignorados']} ignorados, "
            f"{totais['erros']} erros."
        )
        logger.info(msg)
        return {"sucesso": True, "msg": msg, "totais": totais}

    # -------------------------------------------------------------------------
    # Processamento de um pedido
    # -------------------------------------------------------------------------

    def _processar_unico_pedido(self, o: Dict) -> str:
        order_id = o.get('order_id')
        try:
            with transaction.atomic():
                status_obj, _ = StatusPedido.objects.get_or_create(
                    id_baselinker=o.get('order_status_id', 0),
                    defaults={'nome': f"Status {o.get('order_status_id', '?')}"},
                )

                if status_obj.ignorar_atualizacao:
                    if Pedido.objects.filter(id_baselinker=order_id).exists():
                        return 'ignorado'

                defaults = self._montar_defaults_pedido(o, status_obj)
                pedido, created = Pedido.objects.update_or_create(
                    id_baselinker=order_id,
                    defaults=defaults,
                )
                self._sincronizar_itens(pedido, o.get('products', []))
                return 'criado' if created else 'atualizado'

        except Exception as e:
            logger.error(f"Erro no pedido #{order_id}: {e}", exc_info=True)
            return 'erro'

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _ts(timestamp) -> Optional[datetime]:
        try:
            if timestamp and int(timestamp) > 0:
                return make_aware(datetime.fromtimestamp(int(timestamp)))
        except (TypeError, ValueError, OSError):
            pass
        return None

    def _montar_defaults_pedido(self, o: Dict, status_obj: StatusPedido) -> Dict:
        return {
            'id_pedido_loja':        str(o.get('shop_order_id') or ''),
            'id_pedido_externo':     str(o.get('external_order_id') or ''),
            'origem':                o.get('order_source', ''),
            'id_origem':             str(o.get('order_source_id') or ''),
            'status':                status_obj,
            'confirmado':            bool(o.get('confirmed', False)),
            'data_criacao':          self._ts(o.get('date_add')),
            'data_confirmacao':      self._ts(o.get('date_confirmed')),
            'data_no_status':        self._ts(o.get('date_in_status')),
            'email':                 o.get('email', ''),
            'telefone':              o.get('phone', ''),
            'moeda':                 o.get('currency', ''),
            'valor_pago':            o.get('payment_done', 0) or 0,
            'valor_frete':           o.get('delivery_price', 0) or 0,
            'metodo_pagamento':      o.get('payment_method', ''),
            'metodo_entrega':        o.get('delivery_method', ''),
            'numero_rastreio':       o.get('delivery_package_nr', ''),
            'entrega_nome':          o.get('delivery_fullname', ''),
            'entrega_endereco':      o.get('delivery_address', ''),
            'entrega_cep':           o.get('delivery_postcode', ''),
            'entrega_cidade':        o.get('delivery_city', ''),
            'entrega_estado':        o.get('delivery_state', ''),
            'entrega_pais':          o.get('delivery_country', ''),
            'nf_nome':               o.get('invoice_fullname', ''),
            'nf_cnpj_cpf':           o.get('invoice_nip', ''),
            'nf_endereco':           o.get('invoice_address', ''),
            'nf_cidade':             o.get('invoice_city', ''),
            'nf_estado':             o.get('invoice_state', ''),
            'nf_cep':                o.get('invoice_postcode', ''),
            'comentario_comprador':  o.get('user_comments', ''),
            'comentario_vendedor':   o.get('admin_comments', ''),
        }

    @staticmethod
    def _sincronizar_itens(pedido: Pedido, produtos: List[Dict]) -> None:
        for item in produtos:
            item_id = item.get('order_product_id')
            if not item_id:
                continue
            ItemPedido.objects.update_or_create(
                id_item_baselinker=item_id,
                defaults={
                    'pedido':                pedido,
                    'id_produto_baselinker': str(item.get('product_id') or ''),
                    'id_variante':           str(item.get('variant_id') or ''),
                    'nome':                  item.get('name', ''),
                    'sku':                   item.get('sku', ''),
                    'ean':                   item.get('ean', ''),
                    'atributos':             item.get('attributes', ''),
                    'quantidade':            item.get('quantity', 1),
                    'preco_unitario':        item.get('price_brutto', 0) or 0,
                    'taxa':                  item.get('tax_rate', 0) or 0,
                    'peso':                  item.get('weight', 0) or 0,
                },
            )

    def _request_seguro(self, method: str, params: Dict) -> Dict:
        for tentativa in range(1, MAX_RETRIES + 1):
            try:
                resp = self.client._request(method, params)
                if resp.get("status") == "ERROR":
                    raise Exception(f"Erro BaseLinker: {resp.get('error_message', 'desconhecido')}")
                return resp
            except Exception as e:
                msg = str(e).lower()
                is_last = tentativa == MAX_RETRIES
                if "429" in msg or "limit" in msg:
                    logger.warning(f"Rate limit -- tentativa {tentativa}/{MAX_RETRIES}. Aguardando {SLEEP_RATE_LIMIT}s")
                    if not is_last:
                        time.sleep(SLEEP_RATE_LIMIT)
                else:
                    logger.warning(f"Falha na tentativa {tentativa}/{MAX_RETRIES}: {e}")
                    if not is_last:
                        time.sleep(1)
        raise Exception(f"Falha total apos {MAX_RETRIES} tentativas na chamada '{method}'.")
