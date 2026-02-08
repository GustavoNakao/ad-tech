import logging
import time
from typing import Optional, Dict, List
from django.db import transaction, IntegrityError
from baselinker_integracao.client import BaselinkerClient

# Seus models
from ..models.controle_sequencia import ControleSequencia
from ..models.numero_reciclado import NumeroReciclado
from ..models.historico_kit import HistoricoKit

logger = logging.getLogger('gerador_kits')

class CriadorKitsService:
    ID_INVENTARIO = 24542
    
    # --- SEPARAÇÃO DAS CATEGORIAS ---
    # Coloque aqui o ID da categoria "Estruturado" (onde estão os produtos originais 30.xxx)
    ID_CATEGORIA_ORIGEM = 979589  
    
    # ATENÇÃO: Coloque aqui o ID da categoria "Kits" (onde ficam os kits 10.xxx)
    # Se você não souber, pegue na URL do BaseLinker quando clica na categoria de Kits
    ID_CATEGORIA_KITS = 979589  # <--- ALTERE ESTE ID PARA A CATEGORIA DE DESTINO DOS KITS
    
    SKU_GATILHO = 21110
    MAX_RETRIES = 3

    def __init__(self):
        self.client = BaselinkerClient()

    def _request_seguro(self, method: str, params: Dict) -> Dict:
        for tentativa in range(self.MAX_RETRIES):
            try:
                resp = self.client._request(method, params)
                return resp
            except Exception as e:
                msg = str(e)
                if "429" in msg: time.sleep(2)
                logger.warning(f"Tentativa {tentativa+1} falhou: {msg}")
                time.sleep(1)
        raise Exception("Falha na API BaseLinker.")

    # -------------------------------------------------------------------------
    # OPERAÇÕES PRINCIPAIS
    # -------------------------------------------------------------------------

    def executar_criacao(self, pid: str, sku_origem: str, sku_desejado: Optional[str] = None) -> Dict:
        # Baixa da categoria de ORIGEM
        dados = self._baixar_dados_produto(pid)
        if not dados: return {"sucesso": False, "msg": "Erro dados"}

        sku_final = None
        if sku_desejado:
            sku_limpo = str(sku_desejado).strip()
            if self._sku_existe_no_baselinker(sku_limpo):
                return {"sucesso": False, "msg": f"Erro: SKU {sku_limpo} já existe!"}
            sku_final = sku_limpo
        else:
            sku_final = self._gerar_sku_seguro()
            if not sku_final: return {"sucesso": False, "msg": "Erro ao gerar SKU."}

        # Monta payload para a categoria de DESTINO (Kits)
        payload = self._montar_payload(dados, sku_final, pid)
        id_kit = self._enviar_para_baselinker(payload)

        if id_kit:
            try:
                HistoricoKit.objects.create(sku_origem=sku_origem, id_origem=pid, sku_kit=sku_final, id_kit=str(id_kit))
                return {"sucesso": True, "sku_novo": sku_final, "msg": f"Criado: {sku_final}"}
            except IntegrityError:
                return {"sucesso": True, "sku_novo": sku_final, "msg": f"Criado no BL (Já existia no histórico)."}
        else:
            if not sku_desejado: self._reciclar_numero(sku_final)
            return {"sucesso": False, "msg": "Erro API BaseLinker."}

    def buscar_proxima_pendencia(self, ignorados_sessao: List[str]) -> Dict:
        """Busca produtos APENAS na categoria de ORIGEM (Produtos Estruturados)."""
        for pagina in range(1, 6):
            try:
                resp = self._request_seguro("getInventoryProductsList", {
                    "inventory_id": self.ID_INVENTARIO, 
                    "filter_category_id": self.ID_CATEGORIA_ORIGEM, # <--- OLHA SÓ NA ORIGEM
                    "page": pagina
                })
                produtos = resp.get("products", {})
            except Exception as e: return {"status": "ERRO", "msg": str(e)}

            if not produtos: return {"status": "FIM", "msg": "Nenhum produto novo na origem."}

            for pid_str, dados in produtos.items():
                pid = str(pid_str)
                sku = str(dados.get("sku", ""))

                if pid in ignorados_sessao: continue
                if dados.get("is_bundle"): 
                    ignorados_sessao.append(pid); continue
                
                # Valida gatilho
                if not sku.isdigit() or int(sku) <= self.SKU_GATILHO: 
                    ignorados_sessao.append(pid); continue

                hist = HistoricoKit.objects.filter(id_origem=pid).first()
                if hist:
                    if self._produto_existe(hist.id_kit):
                        return {"status": "DUPLICADO", "msg": f"Já existe: {hist.sku_kit}", "pid": pid, "sku_origem": sku}
                    else:
                        self._reciclar_numero(hist.sku_kit); hist.delete()

                return {"status": "NOVO", "pid": pid, "sku_origem": sku, "msg": f"Produto {sku} pronto."}
        return {"status": "FIM", "msg": "Varredura concluída."}

    def calibrar_contador_com_baselinker(self):
        """Calibra olhando APENAS para a categoria de KITS (Destino)."""
        maior_sku = 0
        pagina = 1
        
        while True:
            try:
                # Aqui está o segredo: Filtramos apenas pela categoria de KITS
                resp = self._request_seguro("getInventoryProductsList", {
                    "inventory_id": self.ID_INVENTARIO,
                    "filter_category_id": self.ID_CATEGORIA_KITS, # <--- OLHA SÓ NO DESTINO
                    "page": pagina
                })
                produtos = resp.get("products", {})
            except Exception as e: return {"sucesso": False, "msg": str(e)}

            if not produtos: break

            for dados in produtos.values():
                sku = str(dados.get("sku", ""))
                if not sku.isdigit(): continue
                val = int(sku)

                # Trava de segurança extra: Se achar um SKU gigante (tipo 30k) na categoria de kits, ignora
                if val > 29000: continue 

                if val > maior_sku: maior_sku = val

            pagina += 1
            if pagina > 50: break

        with transaction.atomic():
            seq, _ = ControleSequencia.objects.select_for_update().get_or_create(nome="Sequencia Kits")
            if maior_sku >= seq.proximo_numero:
                seq.proximo_numero = maior_sku + 1
                seq.save()
                msg = f"Calibrado na Categ. Kits! Último: {maior_sku}. Próximo: {seq.proximo_numero}"
            else:
                msg = f"Local ({seq.proximo_numero}) já à frente da Categ. Kits ({maior_sku})."

        return {"sucesso": True, "msg": msg}

    # -------------------------------------------------------------------------
    # MÉTODOS AUXILIARES
    # -------------------------------------------------------------------------

    def _montar_payload(self, dados: Dict, sku_novo: str, pid: str) -> Dict:
        preco = 0.0
        if "prices" in dados and dados["prices"]:
            try: preco = float(next(iter(dados["prices"].values())))
            except: pass

        textos = dados.get("text_fields", {}) or {}
        features = {}
        feat_raw = textos.get("features") or dados.get("features")
        if isinstance(feat_raw, list):
            for i in feat_raw: 
                if isinstance(i, dict): features[i.get("name")] = i.get("value")
        elif isinstance(feat_raw, dict): features = feat_raw

        campos = {"name": textos.get("name") or dados.get("name") or "", 
                  "description": textos.get("description", ""), 
                  "features": features}
        for k, v in textos.items():
            if k.startswith("extra_field_"): campos[k] = v
        campos["extra_field_39310"] = "kit"

        imgs = dados.get("images", [])
        lista_imgs = list(imgs.values()) if isinstance(imgs, dict) else imgs
        imagens = [u for u in lista_imgs if isinstance(u, str) and u.startswith("http")]

        return {
            "inventory_id": self.ID_INVENTARIO, "sku": sku_novo, "ean": dados.get("ean", ""),
            "text_fields": campos, 
            "category_id": self.ID_CATEGORIA_KITS, # <--- SALVA NA CATEGORIA DE DESTINO
            "price_brutto": preco, "weight": dados.get("weight", 0), "images": imagens, 
            "is_bundle": 1, "bundle_products": {str(pid): 1}
        }

    def _gerar_sku_seguro(self) -> Optional[str]:
        for _ in range(10):
            sku_candidato = None
            try:
                with transaction.atomic():
                    reciclado = NumeroReciclado.objects.select_for_update().order_by('numero').first()
                    if reciclado:
                        sku_candidato = str(reciclado.numero); reciclado.delete()
                    else:
                        seq, _ = ControleSequencia.objects.select_for_update().get_or_create(nome="Sequencia Kits")
                        sku_candidato = str(seq.proximo_numero)
                        seq.proximo_numero += 1; seq.save()
            except Exception: continue
            if sku_candidato and not self._sku_existe_no_baselinker(sku_candidato): return sku_candidato
        return None

    def _reciclar_numero(self, sku: str):
        if sku and sku.isdigit(): NumeroReciclado.objects.get_or_create(numero=int(sku))

    def _sku_existe_no_baselinker(self, sku: str) -> bool:
        try:
            r = self.client._request("getInventoryProductsList", {"inventory_id": self.ID_INVENTARIO, "filter_sku": sku})
            return bool(r.get("products"))
        except: return True 

    def _produto_existe(self, id_produto: str) -> bool:
        try:
            r = self.client._request("getInventoryProductsList", {"inventory_id": self.ID_INVENTARIO, "filter_id": int(id_produto)})
            return bool(r.get("products"))
        except: return False

    def _baixar_dados_produto(self, pid: str) -> Optional[Dict]:
        try:
            r = self._request_seguro("getInventoryProductsData", {"inventory_id": self.ID_INVENTARIO, "products": [int(pid)]})
            p = r.get("products", {})
            return p.get(int(pid)) or p.get(str(pid))
        except: return None

    def _enviar_para_baselinker(self, payload: Dict) -> Optional[str]:
        try:
            r = self.client._request("addInventoryProduct", payload)
            return r.get("product_id")
        except Exception as e:
            logger.error(f"❌ Erro API: {e}")
            if "images" in payload:
                payload["images"] = []
                try: r = self.client._request("addInventoryProduct", payload); return r.get("product_id")
                except: pass
            return None