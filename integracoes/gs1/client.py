import base64
import logging
import time
from typing import Optional

from curl_cffi import requests
from decouple import config
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

class GS1AuthError(Exception):
    pass

class GS1Client:
    TOKEN_URL = "https://api.gs1br.org/oauth/access-token"
    BASE_URL  = "https://api.gs1br.org"
    TIMEOUT   = 30

    def __init__(self):
        self._client_id     = config("GS1_CLIENT_ID",     default=None)
        self._client_secret = config("GS1_CLIENT_SECRET", default=None)
        self._username      = config("GS1_USERNAME",      default=None)
        self._password      = config("GS1_PASSWORD",      default=None)
        self._api_key       = config("GS1_API_KEY",       default=None)

        if not all([self._client_id, self._client_secret, self._username, self._password]):
            raise ImproperlyConfigured("Credenciais GS1 incompletas no .env")

        self._access_token:     Optional[str] = None
        self._token_expires_at: float         = 0.0

    def _is_token_valid(self) -> bool:
        return bool(self._access_token) and time.time() < self._token_expires_at

    def _fetch_token(self) -> None:
        credentials = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode()
        ).decode()

        payload = {
            "grant_type": "password",
            "username": self._username,
            "password": self._password,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Basic {credentials}",
        }

        logger.debug(
            "GS1 Auth tentando | username=%s | password=%s | client_id=%s",
            self._username,
            "*" * len(self._password) if self._password else "VAZIO",
            self._client_id,
        )

        try:
            response = requests.post(
                self.TOKEN_URL,
                json=payload,
                headers=headers,
                impersonate="chrome110",
                timeout=self.TIMEOUT,
            )
            logger.debug("GS1 Auth status: %s | body: %s", response.status_code, response.text[:300])
            response.raise_for_status()
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", "N/A")
            body   = getattr(getattr(e, "response", None), "text", str(e))
            logger.error("Erro GS1 Auth. Status: %s - Body: %s", status, body)
            raise GS1AuthError(f"Falha na autenticação GS1: {body}") from e

        data = response.json()
        self._access_token     = data["access_token"]
        self._token_expires_at = time.time() + int(data.get("expires_in", 10800)) - 60
        logger.info("GS1 token obtido com sucesso. Expira em %.0f min.", (self._token_expires_at - time.time()) / 60)

    def get_token(self) -> str:
        if not self._is_token_valid():
            self._fetch_token()
        return self._access_token

    def _session(self):
        token = self.get_token()
        session = requests.Session(impersonate="chrome110")
        session.headers.update({
            "Authorization": f"Bearer {token}",
            "access_token":  token,
            "client_id":     self._client_id,
            "Content-Type":  "application/json",
        })
        if self._api_key:
            session.headers.update({"apikey": self._api_key})
        return session

    @staticmethod
    def _extrair_gtin(data: dict) -> Optional[str]:
        """
        Tenta encontrar o GTIN na resposta da GS1.
        A GS1 pode retornar em diferentes lugares dependendo da versão da API.
        """
        # Tentativa 1: nível raiz
        gtin = data.get("gtin") or data.get("GTIN")
        if gtin:
            return str(gtin)

        # Tentativa 2: dentro de tradeItem
        trade_item = data.get("tradeItem", {})
        gtin = trade_item.get("gtin") or trade_item.get("GTIN")
        if gtin:
            return str(gtin)

        # Tentativa 3: dentro de product.gs1TradeItemIdentificationKey.gtin
        # Estrutura real retornada pela API GS1 Brasil v2:
        # {"result":"SUCCESS","product":{"gs1TradeItemIdentificationKey":{"gtin":"..."}}}
        product = data.get("product", {})
        key = product.get("gs1TradeItemIdentificationKey", {})
        gtin = key.get("gtin") or key.get("gs1TradeItemIdentificationKeyValue")
        if gtin:
            return str(gtin)

        # Tentativa 4: gs1TradeItemIdentificationKey na raiz (versões antigas)
        key = data.get("gs1TradeItemIdentificationKey", {})
        gtin = key.get("gtin") or key.get("gs1TradeItemIdentificationKeyValue")
        if gtin:
            return str(gtin)

        # Tentativa 5: varredura recursiva procurando chave "gtin"
        def _buscar_gtin(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k.lower() == "gtin" and v:
                        return str(v)
                    result = _buscar_gtin(v)
                    if result:
                        return result
            return None

        return _buscar_gtin(data)

    def gerar_ean_completo(self, dados_bl: dict, gpc: str) -> dict:
        session = self._session()
        url = f"{self.BASE_URL}/gs1/v2/products"

        # NCM sem pontos, ex: "9609.10.00" → "96091000"
        ncm_limpo     = str(dados_bl["ncm"]).replace(".", "").strip()
        codigo_origem = str(dados_bl.get("codigo_origem", "076")).strip() or "076"

        # -----------------------------------------------------------------------
        # Payload baseado no Body GTIN_13 oficial do manual GS1 Brasil (nov/2024)
        # Referência: https://portalapi.gs1br.org  →  Manual API CNP v3
        # Apenas campos documentados no manual são enviados para evitar 422.
        # -----------------------------------------------------------------------
        payload = {
            # Mercado alvo e tipo de unidade comercial
            "tradeItem": {
                "targetMarket": {"targetMarketCountryCodes": ["076"]},
                "tradeItemUnitDescriptorCode": "BASE_UNIT_OR_EACH",
            },

            # País/estado de origem (único campo aceito pela API para origem)
            # countrySubdivisionCodes = lista com código BR-UF
            "placeOfProductActivity": {
                "countryOfOrigin": {
                    "countryCode": codigo_origem,
                    "countrySubdivisionCodes": ["BR-SP"],
                }
            },

            # Classificação fiscal
            "tradeItemClassification": {
                "additionalTradeItemClassifications": [
                    {
                        "additionalTradeItemClassificationSystemCode": "NCM",
                        "additionalTradeItemClassificationCodeValue": ncm_limpo,
                    }
                ],
                "gpcCategoryCode": str(gpc).strip(),
            },

            # Empresa dona do CAD GS1
            "company": {"cad": config("GS1_COMPANY_CAD", default="A74646")},

            # Tipo de GTIN a gerar
            "gs1TradeItemIdentificationKey": {
                "gs1TradeItemIdentificationKeyCode": "GTIN_13"
            },

            # Descrição do produto em pt-BR
            "tradeItemDescriptionInformationLang": [
                {
                    "languageCode": "pt-BR",
                    "tradeItemDescription": dados_bl["nome"],
                    "default": True,
                }
            ],

            # Marca
            "brandNameInformationLang": [
                {
                    "languageCode": "pt-BR",
                    "brandName": dados_bl["marca"],
                    "default": True,
                }
            ],

            # Identificação adicional: SKU interno
            # "SKU" é o typeCode correto para exibir como "SKU" no portal GS1 Brasil
            "additionalTradeItemIdentifications": [
                {
                    "additionalTradeItemIdentificationTypeCode": "SKU",
                    "additionalTradeItemIdentificationValue": dados_bl["sku"],
                }
            ],

            # Dimensões e conteúdo líquido
            "tradeItemMeasurements": {
                "depth":      {"measurementUnitCode": "CMT", "value": dados_bl["comprimento_cm"]},
                "height":     {"measurementUnitCode": "CMT", "value": dados_bl["altura_cm"]},
                "width":      {"measurementUnitCode": "CMT", "value": dados_bl["largura_cm"]},
                "netContent": {"measurementUnitCode": "UN",  "value": 1},
            },

            # Peso bruto (obrigatório para ATIVO) e líquido
            "tradeItemWeight": {
                "grossWeight": {
                    "measurementUnitCode": "KGM",
                    "value": dados_bl["peso_kg"],
                },
                "netWeight": {
                    "measurementUnitCode": "KGM",
                    "value": dados_bl.get("peso_liquido_kg") or dados_bl["peso_kg"],
                },
            },

            # Tempo de vida útil mínimo após produção (dias)
            "tradeItemLifespan": {
                "minimumTradeItemLifespanFromTimeOfProduction": 765,
            },

            # Temperatura de armazenamento
            "tradeItemTemperatureInformation": {
                "maximumTemperature": {"measurementUnitCode": "CEL", "value": 40},
                "minimumTemperature": {"measurementUnitCode": "CEL", "value": 0},
            },

            # Flags obrigatórias
            "gtinStatusCode":        "ACTIVE",
            "shareDataIndicator":    True,
            "acceptResponsibility":  True,
            "isNonRetailHealthcareTradeItem": False,
            "inDevelopmentWithoutFeaturedImage": False,
            "inDevelopmentWithoutGrossWeight":   False,
        }

        # ------------------------------------------------------------------
        # referencedFileInformations: imagem obrigatória + link do site
        # Estrutura documentada no manual (GTIN_13 completo, pág. 21-22)
        # ------------------------------------------------------------------
        imagem_url = dados_bl.get("imagem_url", "")
        logger.info("GS1 imagem_url recebida: %r", imagem_url)

        refs = []

        if imagem_url:
            refs.append({
                "languageCode":              "pt-BR",
                "featuredFile":              True,
                "contentDescription":        "Imagem do Produto",
                "fileName":                  "imagem.jpg",
                "uniformResourceIdentifier": imagem_url,
                "default":                   True,
                "referencedFileTypeCode":    "OUT_OF_PACKAGE_IMAGE",
            })

        # Site do produto como OTHER_EXTERNAL_INFORMATION
        # (PRODUCT_WEBSITE não é listado no manual; usar OTHER_EXTERNAL_INFORMATION
        #  conforme exemplo do body completo pág. 21)
        refs.append({
            "languageCode":              None,
            "featuredFile":              False,
            "contentDescription":        "Site do Produto",
            "fileName":                  "",
            "uniformResourceIdentifier": "https://soproduto.com.br/",
            "referencedFileTypeCode":    "OTHER_EXTERNAL_INFORMATION",
            "default":                   False,
        })

        payload["referencedFileInformations"] = refs

        logger.debug("GS1 payload enviado: %s", payload)

        try:
            response = session.post(url, json=payload, timeout=self.TIMEOUT)

            if response.status_code in [200, 201]:
                data = response.json()
                # Log COMPLETO — imprescindível para achar onde está o GTIN
                logger.info("GS1 sucesso body COMPLETO: %s", response.text)

                gtin = self._extrair_gtin(data)
                logger.info("GS1 GTIN extraido: %s", gtin)
                return {"sucesso": True, "dados": {"gtin": gtin, "raw": data}}

            logger.debug("GS1 resposta: status=%s | body=%s", response.status_code, response.text[:800])
            try:
                body      = response.json()
                # Tenta extrair mensagens de validação detalhadas
                validations = body.get("data", {}).get("validations", [])
                if validations:
                    msgs = " | ".join(v.get("message", "") for v in validations)
                    erro_msg = f"Validação GS1: {msgs}"
                else:
                    erro_msg = body.get("message") or response.text
            except Exception:
                erro_msg = response.text

            return {"sucesso": False, "erro": f"GS1 Rejeitou ({response.status_code}): {erro_msg}"}

        except Exception as e:
            return {"sucesso": False, "erro": f"Falha de comunicação GS1: {e}"}

    def consultar_produto(self, gtin: str) -> dict:
        """
        Consulta um produto no Cadastro Nacional de Produtos (CNP) da GS1 pelo GTIN.
        """
        session = self._session()
        
        # Garante GTIN de 14 dígitos (zeros à esquerda)
        gtin_14 = str(gtin).strip().zfill(14)
        url = f"{self.BASE_URL}/gs1/v2/products/{gtin_14}"
        
        logger.info("GS1 consultando produto | GTIN=%s", gtin_14)
        try:
            response = session.get(url, timeout=self.TIMEOUT)
            logger.debug("GS1 consulta status: %s | body: %s", response.status_code, response.text[:800])
            
            if response.status_code == 200:
                return {"sucesso": True, "dados": response.json()}
            
            return {
                "sucesso": False,
                "erro": f"GS1 retornou status {response.status_code}: {response.text[:500]}"
            }
        except Exception as e:
            return {"sucesso": False, "erro": f"Erro de comunicação com a GS1: {e}"}

    def inativar_ou_excluir_produto(self, gtin: str) -> dict:
        """
        Tenta inativar ou excluir um GTIN no Cadastro Nacional de Produtos (CNP) da GS1.
        Tenta primeiro enviar uma chamada DELETE para remoção física imediata. Se falhar,
        realiza o fallback para PATCH definindo o status como INACTIVE.
        """
        session = self._session()
        gtin_14 = str(gtin).strip().zfill(14)
        url = f"{self.BASE_URL}/gs1/v2/products/{gtin_14}"

        logger.warning(f"GS1 tentando inativar/excluir GTIN={gtin_14}")
        
        # Estratégia 1: Tenta remover via DELETE
        try:
            resp_del = session.delete(url, timeout=self.TIMEOUT)
            logger.info(f"GS1 DELETE status={resp_del.status_code} | body={resp_del.text[:300]}")
            if resp_del.status_code in [200, 204]:
                return {"sucesso": True, "msg": f"GTIN {gtin} excluído com sucesso da GS1 via DELETE."}
        except Exception as e:
            logger.error(f"GS1 falha no DELETE do GTIN {gtin}: {e}")

        # Estratégia 2: Fallback para PATCH definindo status como INACTIVE
        payload = {
            "gtinStatusCode": "INACTIVE",
            "acceptResponsibility": True
        }
        try:
            resp_patch = session.patch(url, json=payload, timeout=self.TIMEOUT)
            logger.info(f"GS1 PATCH status={resp_patch.status_code} | body={resp_patch.text[:300]}")
            if resp_patch.status_code in [200, 204, 201]:
                return {"sucesso": True, "msg": f"GTIN {gtin} inativado com sucesso na GS1 via PATCH."}
            
            try:
                body = resp_patch.json()
            except Exception:
                body = {}
            erro_msg = body.get("message") or resp_patch.text
            return {"sucesso": False, "erro": f"GS1 rejeitou atualização para INACTIVE: {erro_msg}"}
        except Exception as e:
            return {"sucesso": False, "erro": f"Falha ao tentar inativar GTIN {gtin} na GS1: {e}"}

    @staticmethod
    def _extrair_gpc(data: dict) -> Optional[str]:
        """
        Tenta encontrar o código GPC (gpcCategoryCode) na resposta da GS1 de forma recursiva.
        """
        # Tentativa 1: na raiz do tradeItemClassification
        classification = data.get("tradeItemClassification") or {}
        gpc = classification.get("gpcCategoryCode")
        if gpc:
            return str(gpc).strip()

        # Tentativa 2: dentro de tradeItem -> tradeItemClassification
        trade_item = data.get("tradeItem") or {}
        classification = trade_item.get("tradeItemClassification") or {}
        gpc = classification.get("gpcCategoryCode")
        if gpc:
            return str(gpc).strip()

        # Tentativa 3: dentro de product -> tradeItemClassification
        product = data.get("product") or {}
        classification = product.get("tradeItemClassification") or {}
        gpc = classification.get("gpcCategoryCode")
        if gpc:
            return str(gpc).strip()

        # Tentativa 4: varredura recursiva procurando chave "gpcCategoryCode"
        def _buscar_gpc(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k.lower() == "gpccategorycode" and v:
                        return str(v).strip()
                    result = _buscar_gpc(v)
                    if result:
                        return result
            elif isinstance(obj, list):
                for item in obj:
                    result = _buscar_gpc(item)
                    if result:
                        return result
            return None

        return _buscar_gpc(data)



# ---------------------------------------------------------------------------
# Singleton — uma única instância por processo.
# O token fica cacheado entre requests; só renova quando expira (~3h).
# ---------------------------------------------------------------------------
_gs1_client_instance: Optional[GS1Client] = None

def get_gs1_client() -> GS1Client:
    global _gs1_client_instance
    if _gs1_client_instance is None:
        _gs1_client_instance = GS1Client()
    return _gs1_client_instance