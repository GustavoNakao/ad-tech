import json
import logging
import re
from typing import Any, Dict, Optional, List

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


class BaselinkerAPIError(Exception):
    """Erro específico da API BaseLinker"""
    pass


class BaselinkerClient:
    BASE_URL = "https://api.baselinker.com/connector.php"
    TIMEOUT = 30

    def __init__(self, token: Optional[str] = None):
        self.token = token or settings.BASELINKER_TOKEN
        if not self.token:
            raise ImproperlyConfigured("BASELINKER_TOKEN não configurado no settings.py")

    def _request(self, method: str, parameters: Optional[Dict] = None) -> Dict[str, Any]:
        parameters = parameters or {}
        payload = {
            "method": method,
            "parameters": json.dumps(parameters, ensure_ascii=False),
        }
        headers = {
            "X-BLToken": self.token,
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "MeuSistema-Django/1.0",
        }
        try:
            response = requests.post(self.BASE_URL, data=payload, headers=headers, timeout=self.TIMEOUT)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error("Erro de rede na API BaseLinker [%s]: %s", method, e)
            raise

        data = response.json()
        if data.get("status") != "SUCCESS":
            logger.warning("BaseLinker API Error [%s]: %s", method, data.get("error_message") or data.get("error_code"))
            raise BaselinkerAPIError(f"BaseLinker API Error: {data.get('error_message') or data.get('error_code')}")
        return data

    def test_connection(self) -> bool:
        try:
            self._request("getStoragesList")
            return True
        except Exception:
            return False

    def get_orders(self, date_from: int) -> list:
        result = self._request("getOrders", {"date_from": date_from})
        return result.get("orders", [])

    def obter_mapa_tags(self, forcar_recarga: bool = False) -> Dict[str, Any]:
        """
        Busca as tags do inventário e cria um dicionário {nome_tag_lower: tag_id_ou_nome}.
        Utiliza cache persistente do Django de 24 horas para evitar requisições redundantes.
        """
        from django.core.cache import cache
        cache_key = "baselinker_mapa_tags"
        
        if not forcar_recarga:
            cached_mapa = cache.get(cache_key)
            if cached_mapa is not None:
                logger.debug("Retornando mapa de tags do BaseLinker a partir do cache")
                return cached_mapa

        try:
            resp = self._request("getInventoryTags", {})
            tags_brutas = resp.get("tags", [])
            
            mapa = {}
            if isinstance(tags_brutas, list):
                # Formato observado: [{'name': 'Classico'}, ...]
                for item in tags_brutas:
                    nome_original = str(item.get("name", "")).strip()
                    if nome_original:
                        mapa[nome_original.lower()] = nome_original
            elif isinstance(tags_brutas, dict):
                # Formato documentado: {id: {"name": ...}}
                for tag_id, dados in tags_brutas.items():
                    nome = str(dados.get("name", "")).strip().lower()
                    if nome:
                        mapa[nome] = int(tag_id)
            
            cache.set(cache_key, mapa, timeout=86400) # Cache por 24 horas
            logger.info("Mapa de tags carregado da API BaseLinker e cacheado por 24h.")
            return mapa
        except Exception as exc:
            logger.error("Falha ao mapear tags do BaseLinker: %s", str(exc), exc_info=True)
            return {}

    def get_inventory_categories(self, inventory_id: Optional[int] = None, forcar_recarga: bool = False) -> List[dict]:
        """
        Busca as categorias do inventário e utiliza cache persistente do Django de 24 horas.
        """
        from django.core.cache import cache
        cache_key = f"baselinker_categories_{inventory_id or 'default'}"
        
        if not forcar_recarga:
            cached = cache.get(cache_key)
            if cached is not None:
                logger.debug("Retornando categorias do BaseLinker a partir do cache")
                return cached

        parameters = {}
        if inventory_id is not None:
            parameters["inventory_id"] = inventory_id
        try:
            result = self._request("getInventoryCategories", parameters)
            categories = result.get("categories", [])
            cache.set(cache_key, categories, timeout=86400) # Cache por 24 horas
            return categories
        except Exception as e:
            logger.error("Erro ao obter categorias do BaseLinker: %s", e)
            return []

    def addInventoryCategory(self, inventory_id: int, name: str, parent_id: int = 0) -> Optional[int]:
        """
        Cria uma nova categoria no inventário do BaseLinker.
        Retorna o category_id gerado ou None em caso de erro.
        """
        params = {
            "inventory_id": inventory_id,
            "name": name,
            "parent_id": parent_id,
        }
        try:
            resp = self._request("addInventoryCategory", params)
            if resp.get("status") == "SUCCESS":
                # Invalida o cache de categorias ao criar uma nova
                from django.core.cache import cache
                cache.delete(f"baselinker_categories_{inventory_id}")
                cache.delete("baselinker_categories_default")
                return resp.get("category_id")
            return None
        except Exception as e:
            logger.error("BaseLinker addInventoryCategory erro: %s", e)
            return None

    def get_inventory_warehouses(self, forcar_recarga: bool = False) -> dict:
        """
        Busca os armazéns do catálogo e utiliza cache persistente de 24 horas.
        """
        from django.core.cache import cache
        cache_key = "baselinker_warehouses"
        
        if not forcar_recarga:
            cached = cache.get(cache_key)
            if cached is not None:
                logger.debug("Retornando armazéns do BaseLinker a partir do cache")
                return cached

        try:
            result = self._request("getInventoryWarehouses")
            cache.set(cache_key, result, timeout=86400) # Cache por 24 horas
            return result
        except Exception as e:
            logger.error("Erro ao obter armazéns do BaseLinker: %s", e)
            return {}

    def get_inventory_price_groups(self, forcar_recarga: bool = False) -> List[dict]:
        """
        Retorna a lista de grupos de preços (tabelas) do BaseLinker.
        Útil para identificar o ID do 'Preço Padrão (BRL)'.
        Utiliza cache persistente de 24 horas.
        """
        from django.core.cache import cache
        cache_key = "baselinker_price_groups"
        
        if not forcar_recarga:
            cached = cache.get(cache_key)
            if cached is not None:
                logger.debug("Retornando grupos de preços do BaseLinker a partir do cache")
                return cached

        try:
            result = self._request("getInventoryPriceGroups")
            price_groups = result.get("price_groups", [])
            cache.set(cache_key, price_groups, timeout=86400) # Cache por 24 horas
            return price_groups
        except Exception as e:
            logger.error("Erro ao obter grupos de preços do BaseLinker: %s", e)
            return []

    @staticmethod
    def _extrair_ncm(features_internas: dict, text_fields: dict) -> str:
        # 1. features dentro de text_fields (estrutura real do BaseLinker)
        for chave in ("NCM", "ncm", "Ncm"):
            valor = str(features_internas.get(chave, "")).strip()
            if valor:
                logger.debug("NCM encontrado em text_fields[features][%s]: %s", chave, valor)
                return valor

        # 2. Varre text_fields pelo formato numérico de NCM
        padrao_ncm = re.compile(r'^\d{4}\.\d{2}\.\d{2}$|^\d{8}$')
        for chave, valor in text_fields.items():
            if chave == "features":
                continue
            v = str(valor).strip()
            if padrao_ncm.match(v):
                logger.debug("NCM encontrado em text_fields[%s] pelo formato: %s", chave, v)
                return v

        # 3. Varre pela chave contendo 'ncm'
        for chave, valor in text_fields.items():
            if "ncm" in str(chave).lower():
                v = str(valor).strip()
                if v:
                    logger.debug("NCM encontrado via chave '%s': %s", chave, v)
                    return v

        return ""

    @staticmethod
    def _extrair_marca(features_internas: dict, text_fields: dict, dados: dict) -> str:
        nomes = ("Marca", "marca", "Brand", "brand", "Fabricante", "fabricante", "Fornecedor", "fornecedor")

        # 1. features dentro de text_fields
        for chave in nomes:
            valor = str(features_internas.get(chave, "")).strip()
            if valor:
                logger.debug("Marca encontrada em text_fields[features][%s]: %s", chave, valor)
                return valor

        # 2. Varre text_fields pela chave
        for chave, valor in text_fields.items():
            if any(k in str(chave).lower() for k in ("marca", "brand", "fabricante", "fornecedor")):
                v = str(valor).strip()
                if v:
                    logger.debug("Marca encontrada em text_fields[%s]: %s", chave, v)
                    return v

        return dados.get("manufacturer_name") or "Marca Não Informada"

    @staticmethod
    def _extrair_codigo_origem(features_internas: dict, text_fields: dict) -> str:
        """
        Extrai o código do país de origem.
        No BaseLinker o campo 'Código da Origem' fica em text_fields como
        'extra_field_XXXXX' com valor tipo '5 - Nacional, mercadoria...'
        O código numérico do país Brasil para a GS1 é sempre '076'.
        Retorna '076' (Brasil) como padrão seguro.
        """
        # Tenta achar em features internas
        for chave in ("Código da Origem", "codigo_origem", "Origem", "origem", "Pais Origem"):
            valor = str(features_internas.get(chave, "")).strip()
            if valor:
                return "076"  # Se origem for Nacional, é Brasil = 076

        # Varre text_fields pela chave
        for chave, valor in text_fields.items():
            if any(k in str(chave).lower() for k in ("origem", "origin", "pais")):
                v = str(valor).strip()
                if v:
                    return "076"  # Nacional = Brasil = 076

        return "076"  # Padrão Brasil

    def buscar_produto_por_sku(self, sku: str, inventory_id: int = 24542) -> Optional[dict]:
        try:
            res_lista = self._request("getInventoryProductsList", {"inventory_id": inventory_id, "filter_sku": sku})
            produtos_encontrados = res_lista.get("products", {})

            if not produtos_encontrados:
                logger.warning("SKU %s não encontrado no catálogo %s.", sku, inventory_id)
                return None

            id_produto = list(produtos_encontrados.keys())[0]

            res_dados = self._request("getInventoryProductsData", {"inventory_id": inventory_id, "products": [int(id_produto)]})
            dados = res_dados.get("products", {}).get(str(id_produto))

            if not dados:
                return None

            text_fields      = dados.get("text_fields", {})
            features_raiz    = dados.get("features", {})
            features_internas = text_fields.get("features", {})

            logger.debug(
                "BaseLinker SKU %s | manufacturer_name=%s | features_raiz=%s | text_fields[features]=%s",
                sku, dados.get("manufacturer_name"), features_raiz, features_internas,
            )

            ncm           = self._extrair_ncm(features_internas, text_fields)
            marca         = self._extrair_marca(features_internas, text_fields, dados)
            codigo_origem = self._extrair_codigo_origem(features_internas, text_fields)

            logger.info("SKU %s resolvido | NCM=%s | Marca=%s | Origem=%s",
                        sku, ncm or "NÃO ENCONTRADO", marca, codigo_origem)

            # --- Extração de imagem robusta ---
            imagem_url = ""
            imgs_raw = dados.get("images", {})
            logger.info("SKU %s | images bruto do BaseLinker: %r", sku, imgs_raw)
            if isinstance(imgs_raw, dict) and imgs_raw:
                lista_urls = []
                for v in imgs_raw.values():
                    url_limpa = ""
                    if isinstance(v, str):
                        if v.startswith("url:"):
                            url_limpa = v[4:].strip()
                        elif v.startswith("http"):
                            url_limpa = v.strip()
                    elif isinstance(v, dict):
                        url_limpa = (v.get("url") or v.get("src") or "").strip()
                    if url_limpa:
                        lista_urls.append(url_limpa)

                # Prioriza imagens que não sejam .webp
                url_selecionada = ""
                for u in lista_urls:
                    path_part = u.split("?")[0].lower()
                    if not path_part.endswith(".webp") and ".webp" not in path_part:
                        url_selecionada = u
                        break

                # Se não encontrou nenhuma não-webp, usa a primeira
                if not url_selecionada and lista_urls:
                    url_selecionada = lista_urls[0]

                imagem_url = url_selecionada
            logger.info("SKU %s | imagem_url final extraída: %r", sku, imagem_url)


            return {
                "id_produto":      id_produto,
                "sku":             sku,
                "nome":            dados.get("name", "") or text_fields.get("name", ""),
                "name":            dados.get("name", "") or text_fields.get("name", ""), # Compatibilidade
                "peso_kg":         float(dados.get("weight", 0) or 0),
                "weight":          float(dados.get("weight", 0) or 0), # Compatibilidade
                "comprimento_cm":  float(dados.get("length", 0) or 0),
                "length":          float(dados.get("length", 0) or 0), # Compatibilidade
                "largura_cm":      float(dados.get("width", 0) or 0),
                "width":           float(dados.get("width", 0) or 0), # Compatibilidade
                "altura_cm":       float(dados.get("height", 0) or 0),
                "height":          float(dados.get("height", 0) or 0), # Compatibilidade
                "average_cost":    float(dados.get("average_cost", 0) or 0),
                "manufacturer_id": dados.get("manufacturer_id", 0),
                "ncm":             ncm,
                "marca":           marca,
                "codigo_origem":   codigo_origem,
                "imagem_url":      imagem_url,
                "images":          dados.get("images", {}), # Mantém para extração posterior
                "ean":             dados.get("ean", ""),
                "price_brutto":    float(dados.get("price_brutto", 0) or 0),
                "prices":          dados.get("prices", {}),
                "tax_rate":        dados.get("tax_rate", 0),
                "text_fields":     text_fields, # Mantém para compatibilidade
            }

        except Exception as e:
            logger.error("Erro BaseLinker ao buscar SKU %s: %s", sku, e)
            return None

    # ── Extra Fields ──────────────────────────────────────────────────────────

    def get_inventory_extra_fields(self) -> Dict[str, Dict]:
        """
        Retorna todos os campos extras do catálogo BaseLinker.
        Estrutura retornada: {field_id_str: {"name": "...", "editor_type": "...", ...}}

        Documentação: https://api.baselinker.com/index.php?method=getInventoryExtraFields
        """
        result = self._request("getInventoryExtraFields")
        campos = result.get("extra_fields", {})
        if isinstance(campos, list):
            campos = {}
        logger.debug("BaseLinker extra_fields disponíveis: %s", list(campos.keys()))
        return campos

    def resolver_campo_extra(self, nome_campo: str) -> Optional[str]:
        """
        Dado o nome legível de um campo extra (ex: "ML Category ID"),
        retorna a chave correta para usar no payload (ex: "extra_field_39999").

        Retorna None se o campo não existir — nesse caso o campo deve ser
        criado manualmente no painel BL: Catálogo → Campos adicionais.
        """
        campos = self.get_inventory_extra_fields()
        for field_id, info in campos.items():
            if isinstance(info, dict) and info.get("name", "").strip().lower() == nome_campo.strip().lower():
                chave = f"extra_field_{field_id}"
                logger.info("Campo extra '%s' resolvido → %s", nome_campo, chave)
                return chave
        logger.warning(
            "Campo extra '%s' não encontrado no BaseLinker. "
            "Crie-o em: Painel BL → Catálogo → Campos adicionais.",
            nome_campo,
        )
        return None

    def salvar_ean_no_produto(self, inventory_id: int, product_id: int, ean: str) -> bool:
        # addInventoryProduct com product_id existente faz UPDATE do produto.
        # Apenas inventory_id, product_id e ean sao enviados — demais campos permanecem intactos.
        # Ref: https://api.baselinker.com/index.php?method=addInventoryProduct
        params = {
            "inventory_id": str(inventory_id),
            "product_id":   str(product_id),
            "ean":          str(ean),
        }
        try:
            resp = self._request("addInventoryProduct", params)
            return resp.get("status") == "SUCCESS"
        except Exception as e:
            logger.error("BaseLinker salvar_ean_no_produto erro: %s", e)
            return False