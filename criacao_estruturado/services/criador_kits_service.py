import logging
import time
from typing import Optional, Dict, List
from django.db import connection
from baselinker_integracao.client import BaselinkerClient

# Seus models
from ..models.historico_kit import HistoricoKit
from ..models.numero_reciclado import NumeroReciclado

# Configura o logger para imprimir no terminal
logger = logging.getLogger('gerador_kits')

class CriadorKitsService:
    ID_INVENTARIO = 24542
    
    # ID da categoria onde estão os produtos (Misturados)
    ID_CATEGORIA_ORIGEM = 979589
    ID_CATEGORIA_KITS = 979589
        
    # Onde começa a série de Kits
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
                if "429" in msg: time.sleep(2)
                logger.warning(f"⚠️ Tentativa {tentativa+1} falhou: {msg}")
                time.sleep(1)
        raise Exception("Falha na API BaseLinker após tentativas.")

    # -------------------------------------------------------------------------
    # 1. CALIBRAGEM INTELIGENTE (TAPAR BURACOS + CHECK IS_BUNDLE)
    # -------------------------------------------------------------------------
    def calibrar_contador_com_baselinker(self):
        """
        Baixa todos os Kits existentes e encontra o PRIMEIRO BURACO disponível.
        Ignora produtos da série 30k (is_bundle=false).
        """
        skus_encontrados = set()
        pagina = 1
        
        logger.info("🔄 Iniciando Calibragem...")

        while True:
            try:
                # 1. LISTAR IDs
                resp_list = self._request_seguro("getInventoryProductsList", {
                    "inventory_id": self.ID_INVENTARIO,
                    "filter_category_id": self.ID_CATEGORIA_ORIGEM, 
                    "page": pagina
                })
                lista_produtos = resp_list.get("products", {})
            except Exception as e:
                return {"sucesso": False, "msg": f"Erro API List: {e}"}

            if not lista_produtos: break

            ids_da_pagina = list(lista_produtos.keys())
            
            # 2. VERIFICAR (Check is_bundle real)
            try:
                resp_data = self._request_seguro("getInventoryProductsData", {
                    "inventory_id": self.ID_INVENTARIO,
                    "products": ids_da_pagina
                })
                detalhes_produtos = resp_data.get("products", {})
            except Exception:
                detalhes_produtos = {} 

            # 3. FILTRAR E GUARDAR
            for pid, dados in detalhes_produtos.items():
                sku = str(dados.get("sku", ""))
                if not sku.isdigit(): continue
                val = int(sku)

                is_bundle = dados.get("is_bundle", False)

                if is_bundle:
                    # É KIT: Guarda na lista de "ocupados"
                    skus_encontrados.add(val)
                else:
                    # É PRODUTO 30k: Ignora completamente
                    continue

            pagina += 1
            if pagina > 50: break 

        # 4. ALGORITMO DE "GAP FILLING" (Achar o buraco)
        if not skus_encontrados:
            proximo_livre = self.INICIO_SERIE
        else:
            # Começa do 10690 e vai subindo 1 por 1 até achar um livre
            proximo_livre = self.INICIO_SERIE
            while proximo_livre in skus_encontrados:
                proximo_livre += 1
        
        # Atualiza o Banco com o BURACO encontrado
        with connection.cursor() as cursor:
            cursor.execute("UPDATE public.controle_sequencia SET proximo_numero = %s WHERE id = 1", [proximo_livre])
            # Limpa a lixeira para não interferir na lógica do buraco
            cursor.execute("DELETE FROM public.numero_reciclado")

        logger.info(f"✅ Calibrado! Próximo SKU definido: {proximo_livre}")
        return {"sucesso": True, "msg": f"Calibrado! Buracos verificados. Contador definido para: {proximo_livre}"}

    # -------------------------------------------------------------------------
    # 2. BUSCA DE PENDÊNCIAS
    # -------------------------------------------------------------------------
    def buscar_proxima_pendencia(self, ignorados_sessao: List[str]) -> Dict:
        """Busca produtos (is_bundle=false) para transformar em Kit."""
        for pagina in range(1, 6):
            try:
                resp_list = self._request_seguro("getInventoryProductsList", {
                    "inventory_id": self.ID_INVENTARIO, 
                    "filter_category_id": self.ID_CATEGORIA_ORIGEM, 
                    "page": pagina
                })
                lista_produtos = resp_list.get("products", {})
            except Exception as e: return {"status": "ERRO", "msg": str(e)}

            if not lista_produtos: return {"status": "FIM", "msg": "Varredura concluída."}

            ids_check = [pid for pid in lista_produtos.keys() if str(pid) not in ignorados_sessao]
            if not ids_check: continue

            try:
                resp_data = self._request_seguro("getInventoryProductsData", {
                    "inventory_id": self.ID_INVENTARIO,
                    "products": ids_check
                })
                detalhes = resp_data.get("products", {})
            except: continue

            for pid_str, dados in detalhes.items():
                pid = str(pid_str)
                sku = str(dados.get("sku", ""))
                is_bundle = dados.get("is_bundle", False)

                if is_bundle:
                    ignorados_sessao.append(pid) # É kit, ignora
                    continue
                else:
                    return {
                        "status": "NOVO", 
                        "pid": pid, 
                        "sku_origem": sku, 
                        "msg": f"Produto {sku} encontrado."
                    }

        return {"status": "FIM", "msg": "Sem pendências."}

    # -------------------------------------------------------------------------
    # 3. CRIAÇÃO COM LOGS, IMAGENS CORRIGIDAS E DETECÇÃO DE DUPLICIDADE
    # -------------------------------------------------------------------------
    def executar_criacao(self, pid: str, sku_origem: str, sku_desejado: Optional[str] = None) -> Dict:
        dados = self._baixar_dados_produto(pid)
        if not dados: return {"sucesso": False, "msg": "Erro ao baixar dados do produto."}

        # Tenta até 5 vezes (caso bata num SKU ocupado/muro)
        for tentativa in range(5):
            
            # A. Obtém SKU
            sku_final = None
            if sku_desejado:
                sku_final = str(sku_desejado)
            else:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT get_next_kit_sku()")
                        row = cursor.fetchone()
                        if row: sku_final = str(row[0])
                except Exception as e: return {"sucesso": False, "msg": f"DB Erro: {e}"}

            if not sku_final: return {"sucesso": False, "msg": "Sem SKU disponível."}

            # B. Tenta Criar
            try:
                payload = self._montar_payload(dados, sku_final, pid)
                id_kit = self._enviar_para_baselinker(payload)

                if id_kit:
                    # SUCESSO!
                    try:
                        HistoricoKit.objects.create(sku_origem=sku_origem, id_origem=pid, sku_kit=sku_final, id_kit=str(id_kit))
                    except: pass
                    return {"sucesso": True, "sku_novo": sku_final, "msg": f"Kit {sku_final} criado com sucesso!"}

            except Exception as e:
                erro_msg = str(e).lower()
                logger.error(f"💥 Erro capturado no loop: {erro_msg}") 

                # LISTA DE PALAVRAS QUE INDICAM DUPLICIDADE
                termos_duplicidade = ["exist", "já existe", "already", "duplica", "taken", "em uso", "sku"]
                eh_duplicado = any(t in erro_msg for t in termos_duplicidade)

                if eh_duplicado:
                    logger.warning(f"🧱 Muro detectado (SKU {sku_final} ocupado). Tentando próximo...")
                    
                    if sku_desejado: 
                        return {"sucesso": False, "msg": f"Erro: SKU {sku_final} já existe."}
                    
                    # NÃO RECICLA! Continua o loop para pegar o próximo número.
                    continue 
                
                else:
                    # ERRO REAL (Preço, Imagem, etc)
                    self._reciclar_numero(sku_final)
                    return {"sucesso": False, "msg": f"ERRO API: {str(e)}"}

        return {"sucesso": False, "msg": "Falha após 5 tentativas de SKU sequenciais."}

    # --- UTILITÁRIOS ---
    
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

    def buscar_pid_por_sku(self, sku: str) -> Optional[str]:
        try:
            resp = self._request_seguro("getInventoryProductsList", {
                "inventory_id": self.ID_INVENTARIO,
                "filter_sku": str(sku)
            })
            produtos = resp.get("products", {})
            if not produtos: return None
            return str(next(iter(produtos.keys())))
        except: return None

    def _reciclar_numero(self, sku: str):
        if sku and sku.isdigit():
            try:
                with connection.cursor() as cursor:
                    cursor.execute("INSERT INTO public.numero_reciclado (numero) VALUES (%s) ON CONFLICT DO NOTHING", [int(sku)])
            except: pass

    def _baixar_dados_produto(self, pid: str) -> Optional[Dict]:
        try:
            r = self._request_seguro("getInventoryProductsData", {"inventory_id": self.ID_INVENTARIO, "products": [int(pid)]})
            p = r.get("products", {})
            return p.get(int(pid)) or p.get(str(pid))
        except: return None

    def _montar_payload(self, dados: Dict, sku_novo: str, pid: str) -> Dict:
        # 1. Preço
        preco = 0.0
        if "prices" in dados and isinstance(dados["prices"], dict):
            try: preco = float(next(iter(dados["prices"].values())))
            except: pass

        # 2. Textos e Features
        textos = dados.get("text_fields", {}) or {}
        features = {}
        feat_raw = textos.get("features") or dados.get("features")
        
        if isinstance(feat_raw, list):
            for i in feat_raw: 
                if isinstance(i, dict): features[i.get("name")] = i.get("value")
        elif isinstance(feat_raw, dict): features = feat_raw

        campos = {
            "name": textos.get("name") or dados.get("name"), 
            "description": textos.get("description", ""), 
            "features": features
        }
        for k, v in textos.items():
            if k.startswith("extra_field_"): campos[k] = v
        
        campos["extra_field_39310"] = "kit"

        # 3. Imagens
        imgs_raw = dados.get("images") or []
        lista_urls = []

        if isinstance(imgs_raw, dict):
            lista_urls = list(imgs_raw.values())
        elif isinstance(imgs_raw, list):
            lista_urls = imgs_raw
        
        urls_validas = [u for u in lista_urls if isinstance(u, str) and u.startswith("http")]

        # BaseLinker prefere dicionário indexado
        # Nota: Se "url:" estava dando certo pra você, mantive. 
        # Se der erro, tente remover o "url:" e deixar só a variavel url.
        imagens_formatadas = {str(i): f"url:{url}" for i, url in enumerate(urls_validas)}

        return {
            "inventory_id": self.ID_INVENTARIO, 
            "sku": sku_novo, 
            "ean": dados.get("ean", ""),
            "text_fields": campos, 
            "category_id": self.ID_CATEGORIA_KITS, 
            "price_brutto": preco, 
            
            # --- CORREÇÃO: DIMENSÕES E PESO ---
            "weight": dados.get("weight", 0),
            "height": dados.get("height", 0), # Altura
            "width": dados.get("width", 0),   # Largura
            "length": dados.get("length", 0), # Comprimento (Profundidade)
            # ----------------------------------
            
            "images": imagens_formatadas, 
            "is_bundle": 1, 
            "bundle_products": {str(pid): 1}
        }