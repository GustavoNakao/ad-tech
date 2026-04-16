import logging
import time
import os
from datetime import datetime
from typing import Dict
from django.db import transaction
from django.utils.timezone import make_aware

# Bibliotecas de integração e Models
from baselinker_integracao.client import BaselinkerClient 
from pedidos.models.status import StatusPedido
from pedidos.models.pedido import Pedido
from pedidos.models.item import ItemPedido

logger = logging.getLogger('sincronizador_pedidos')

class SincronizadorPedidosService:
    MAX_RETRIES = 3

    def __init__(self):
        self.client = BaselinkerClient()
        # O Token deve estar no seu arquivo .env
        self.token = os.getenv("BASELINKER_TOKEN")

    def _request_seguro(self, method: str, params: Dict) -> Dict:
        """Gerencia requisições com retry e tratamento de erro da API."""
        for tentativa in range(self.MAX_RETRIES):
            try:
                resp = self.client._request(method, params)
                
                if resp.get("status") == "ERROR":
                    # Captura erro de token inválido ou outros da API
                    error_msg = resp.get("error_message", "Erro desconhecido")
                    raise Exception(f"Erro BaseLinker: {error_msg}")
                
                return resp
            except Exception as e:
                msg = str(e).lower()
                if "429" in msg or "limit" in msg:
                    logger.warning(f"⏳ Rate limit. Tentativa {tentativa+1}. Dormindo 2s...")
                    time.sleep(2)
                else:
                    logger.warning(f"⚠️ Falha na tentativa {tentativa+1}: {e}")
                    time.sleep(1)
        
        raise Exception("Falha total na comunicação com BaseLinker.")

    def executar_sincronizacao(self, dias_atras: int = 7) -> Dict:
        """Faz a varredura e processa os blocos de pedidos."""
        timestamp_filtro = int(time.time()) - (dias_atras * 24 * 60 * 60)
        total_processado = 0
        
        while True:
            params = {
                "date_confirmed_from": timestamp_filtro,
                "get_unconfirmed_orders": False,
            }

            try:
                resp = self._request_seguro("getOrders", params)
                orders = resp.get("orders", [])
            except Exception as e:
                return {"sucesso": False, "msg": str(e)}

            if not orders:
                break

            for pedido_data in orders:
                if self._processar_unico_pedido(pedido_data):
                    total_processado += 1

            if len(orders) < 100:
                break
            
            # Evita duplicidade na próxima página
            ultimo_ts = max(o['date_confirmed'] for o in orders if o.get('date_confirmed'))
            timestamp_filtro = ultimo_ts + 1
            time.sleep(0.5)

        return {"sucesso": True, "msg": f"Sincronização finalizada! {total_processado} pedidos processados."}

    def _processar_unico_pedido(self, o: Dict) -> bool:
        """Salva o pedido e itens no banco de dados de forma atômica."""
        try:
            with transaction.atomic():
                # 1. Garante que o status existe no banco
                status_obj, _ = StatusPedido.objects.get_or_create(
                    id_baselinker=o['order_status_id'],
                    defaults={'nome': f"Status {o['order_status_id']}"}
                )

                # 2. Verifica se o status atual permite atualizações
                pedido_existente = Pedido.objects.filter(id_baselinker=o['order_id']).first()
                if status_obj.ignorar_atualizacao and pedido_existente:
                    return False

                def to_dt(ts):
                    return make_aware(datetime.fromtimestamp(ts)) if ts and ts > 0 else None

                # 3. Salva/Atualiza a capa do pedido
                pedido, created = Pedido.objects.update_or_create(
                    id_baselinker=o['order_id'],
                    defaults={
                        'id_pedido_loja': o.get('shop_order_id', ''),
                        'origem': o.get('order_source', ''),
                        'status': status_obj,
                        'email': o.get('email', ''),
                        'valor_pago': o.get('payment_done', 0),
                        'data_criacao': to_dt(o.get('date_add')),
                        'data_confirmacao': to_dt(o.get('date_confirmed')),
                    }
                )

                # 4. Salva os itens vinculados
                for item in o.get('products', []):
                    ItemPedido.objects.update_or_create(
                        id_item_baselinker=item['order_product_id'],
                        defaults={
                            'pedido': pedido,
                            'sku': item.get('sku', ''),
                            'nome': item.get('name', ''),
                            'quantidade': item.get('quantity', 1),
                            'preco_unitario': item.get('price_brutto', 0),
                        }
                    )
                return True
        except Exception as e:
            logger.error(f"💥 Falha no pedido {o.get('order_id')}: {e}")
            return False