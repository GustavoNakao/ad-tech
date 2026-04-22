import logging
import time
from typing import Optional, Dict
from django.db import connection
from integracoes.baselinker.client import BaselinkerClient

from ..models.historico_kit import HistoricoKit

logger = logging.getLogger('gerador_kits')


class CriadorKitsService:
    ID_INVENTARIO = 24542
    ID_CATEGORIA_KITS = 979589
    INICIO_SERIE = 10764
    MAX_RETRIES = 3

    def __init__(self):
        self.client = BaselinkerClient()

    def _request_seguro(self, method: str, params: Dict) -> Dict:
        """Faz requisição à API com retry automático."""
        for tentativa in range(self.MAX_RETRIES):
            try:
                resp = self.client._request(method, params)
                return resp
            except Exception as e:
                msg = str(e)
                if "429" in msg:
                    time.sleep(2)
                logger.warning(f"⚠️ Tentativa {tentativa+1} falhou: {msg}")
                time.sleep(1)
        raise Exception("Falha na API BaseLinker após tentativas.")

    # -------------------------------------------------------------------------
    # BUSCA DE PRODUTO POR SKU
    # -------------------------------------------------------------------------
    def buscar_pid_por_sku(self, sku: str) -> Optional[str]:
        try:
            resp = self._request_seguro("getInventoryProductsList", {
                "inventory_id": self.ID_INVENTARIO,
                "filter_sku": str(sku)
            })
            produtos = resp.get("products", {})
            if not produtos:
                return None
            return str(next(iter(produtos.keys())))
        except Exception:
            return None

    # -------------------------------------------------------------------------
    # CRIAÇÃO DO KIT
    # -------------------------------------------------------------------------
    def executar_criacao(self, pid: str, sku_origem: str, sku_desejado: Optional[str] = None) -> Dict:
        dados = self._baixar_dados_produto(pid)
        if not dados:
            return {"sucesso": False, "msg": "Erro ao baixar dados do produto."}

        for tentativa in range(5):

            sku_final = None
            if sku_desejado:
                sku_final = str(sku_desejado)
            else:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT get_next_kit_sku()")
                        row = cursor.fetchone()
                        if row:
                            sku_final = str(row[0])
                except Exception as e:
                    return {"sucesso": False, "msg": f"DB Erro: {e}"}

            if not sku_final:
                return {"sucesso": False, "msg": "Sem SKU disponível."}

            try:
                payload = self._montar_payload(dados, sku_final, pid)
                id_kit = self._enviar_para_baselinker(payload)

                if id_kit:
                    try:
                        HistoricoKit.objects.create(
                            sku_origem=sku_origem,
                            id_origem=pid,
                            sku_kit=sku_final,
                            id_kit=str(id_kit)
                        )
                    except Exception:
                        pass
                    return {"sucesso": True, "sku_novo": sku_final, "msg": f"Kit {sku_final} criado com sucesso!"}

            except Exception as e:
                erro_msg = str(e).lower()
                logger.error(f"💥 Erro capturado: {erro_msg}")

                termos_duplicidade = ["exist", "já existe", "already", "duplica", "taken", "em uso", "sku"]
                eh_duplicado = any(t in erro_msg for t in termos_duplicidade)

                if eh_duplicado:
                    logger.warning(f"🧱 SKU {sku_final} ocupado. Tentando próximo...")
                    if sku_desejado:
                        return {"sucesso": False, "msg": f"Erro: SKU {sku_final} já existe."}
                    continue
                else:
                    self._reciclar_numero(sku_final)
                    return {"sucesso": False, "msg": f"ERRO API: {str(e)}"}

        return {"sucesso": False, "msg": "Falha após 5 tentativas de SKU sequenciais."}

    # -------------------------------------------------------------------------
    # UTILITÁRIOS INTERNOS
    # -------------------------------------------------------------------------
    def _enviar_para_baselinker(self, payload: Dict) -> Optional[str]:
        sku = payload.get('sku', '???')
        try:
            logger.info(f"📤 Enviando SKU {sku}...")
            r = self.client._request("addInventoryProduct", payload)
            pid = r.get("product_id")
            logger.info(f"✅ Sucesso! ID: {pid}")
            return pid
        except Exception as e:
            logger.error(f"❌ FALHA BASELINKER NO SKU {sku}: {str(e)}")
            raise e

    def _reciclar_numero(self, sku: str):
        if sku and sku.isdigit():
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO public.numero_reciclado (numero) VALUES (%s) ON CONFLICT DO NOTHING",
                        [int(sku)]
                    )
            except Exception:
                pass

    def _baixar_dados_produto(self, pid: str) -> Optional[Dict]:
        try:
            r = self._request_seguro("getInventoryProductsData", {
                "inventory_id": self.ID_INVENTARIO,
                "products": [int(pid)]
            })
            p = r.get("products", {})
            return p.get(int(pid)) or p.get(str(pid))
        except Exception:
            return None

    def _montar_payload(self, dados: Dict, sku_novo: str, pid: str) -> Dict:
        # 1. Preço
        preco = 0.0
        if "prices" in dados and isinstance(dados["prices"], dict):
            try:
                preco = float(next(iter(dados["prices"].values())))
            except Exception:
                pass

        # 2. Textos e extra_fields
        textos = dados.get("text_fields", {}) or {}

        campos = {
            "name": textos.get("name") or dados.get("name"),
            "description": textos.get("description", ""),
        }

        # Copia todos os extra_fields do produto original
        for k, v in textos.items():
            if k.startswith("extra_field_"):
                campos[k] = v

        # Tipo de unidade → padrão "un"
        campos["extra_field_39310"] = "un"
        # Tipo da Unidade (extra_field_50847) → copia do produto original (já incluído no loop acima)

        # 3. Features (especificações)
        features = {}
        feat_raw = textos.get("features") or dados.get("features")
        if isinstance(feat_raw, list):
            for i in feat_raw:
                if isinstance(i, dict):
                    features[i.get("name")] = i.get("value")
        elif isinstance(feat_raw, dict):
            features = feat_raw
        campos["features"] = features

        # 4. Imagens
        imgs_raw = dados.get("images") or []
        lista_urls = []
        if isinstance(imgs_raw, dict):
            lista_urls = list(imgs_raw.values())
        elif isinstance(imgs_raw, list):
            lista_urls = imgs_raw
        urls_validas = [u for u in lista_urls if isinstance(u, str) and u.startswith("http")]
        imagens_formatadas = {str(i): f"url:{url}" for i, url in enumerate(urls_validas)}

        # 5. FIX: Fabricante — campo "manufacturer_id" da API Baselinker (ID numérico)
        manufacturer_id = dados.get("manufacturer_id") or 0

        return {
            "inventory_id": self.ID_INVENTARIO,
            "sku": sku_novo,
            "ean": dados.get("ean", ""),
            "manufacturer_id": manufacturer_id,
            "text_fields": campos,
            "category_id": self.ID_CATEGORIA_KITS,
            "price_brutto": preco,
            "weight": dados.get("weight", 0),
            "height": dados.get("height", 0),
            "width": dados.get("width", 0),
            "length": dados.get("length", 0),
            "images": imagens_formatadas,
            "is_bundle": 1,
            "bundle_products": {str(pid): 1},
        }
