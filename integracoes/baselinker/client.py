# baselink_sync/client.py
import json
import logging
from typing import Any, Dict, Optional, List

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


class BaselinkerAPIError(Exception):
    """Erro específico da API BaseLinker"""
    pass


class BaselinkerClient:
    """
    Cliente oficial da API BaseLinker – padrão 2025 (X-BLToken no header)
    """
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
            response = requests.post(
                self.BASE_URL,
                data=payload,
                headers=headers,
                timeout=self.TIMEOUT,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error("Erro de rede na API BaseLinker [%s]: %s", method, e)
            raise

        data = response.json()

        if data.get("status") != "SUCCESS":
            logger.warning("BaseLinker API Error [%s]: %s", method, data.get("error_message") or data.get("error_code"))
            raise BaselinkerAPIError(f"BaseLinker API Error: {data.get('error_message') or data.get('error_code')}")

        return data

    # ==============================================================
    # MÉTODOS PRONTOS PARA USO IMEDIATO
    # ==============================================================

    def test_connection(self) -> bool:
        """Testa se o token está funcionando"""
        try:
            self._request("getStoragesList")
            return True
        except Exception:
            return False

    def get_orders(self, date_from: int) -> list:
        params = {"date_from": date_from}
        result = self._request("getOrders", params)
        return result.get("orders", [])

    # No seu BaselinkerClient (já está certo!)
    def get_inventory_categories(self, inventory_id: Optional[int] = None) -> List[dict]:
        """
        Puxa TODAS as categorias de TODOS os catálogos (deixa inventory_id vazio)
        """
        parameters = {}
        if inventory_id is not None:
            parameters["inventory_id"] = inventory_id

        result = self._request("getInventoryCategories", parameters)
        return result.get("categories", [])

    def get_inventory_warehouses(self) -> dict:
        """
        Busca a lista de armazéns internos do BaseLinker.
        Endpoint: getInventoryWarehouses
        """
        # Este método retorna um dicionário contendo a chave "warehouses"
        return self._request("getInventoryWarehouses")


    # baselinker_integracao/client.py (adicione isso se ainda não tiver)

    def get_inventories(self):
        return self._request("getInventories")

    def get_products_list(self, inventory_id, category_id, page):
        return self._request("getInventoryProductsList", {...})

    def get_product_data(self, inventory_id, product_id):
        return self._request("getInventoryProductsData", {...})
