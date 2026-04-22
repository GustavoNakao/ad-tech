import logging
import time
import requests
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
        self._api_key       = config("GS1_API_KEY",       default=None)

        if not all([self._client_id, self._client_secret, self._api_key]):
            raise ImproperlyConfigured("GS1_CLIENT_ID, GS1_CLIENT_SECRET e GS1_API_KEY precisam estar configurados.")

        self._access_token:    str | None = None
        self._token_expires_at: float     = 0.0

    def _is_token_valid(self) -> bool:
        return bool(self._access_token) and time.time() < self._token_expires_at

    def _fetch_token(self) -> None:
        payload = {
            "grant_type":    "client_credentials",
            "client_id":     self._client_id,
            "client_secret": self._client_secret,
        }
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "apikey":       self._api_key,
            "User-Agent":   "MeuSistema-Django/1.0",
        }

        try:
            response = requests.post(self.TOKEN_URL, data=payload, headers=headers, timeout=self.TIMEOUT)
            response.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error("Erro de rede ao autenticar na GS1: %s", e)
            raise

        data = response.json()
        self._access_token      = data["access_token"]
        self._token_expires_at  = time.time() + int(data.get("expires_in", 3600)) - 30
        logger.info("Token GS1 obtido com sucesso.")

    def get_token(self) -> str:
        if not self._is_token_valid():
            self._fetch_token()
        return self._access_token

    def _session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({
            "Authorization": f"Bearer {self.get_token()}",
            "apikey":        self._api_key,
            "Content-Type":  "application/json",
            "User-Agent":    "MeuSistema-Django/1.0",
        })
        return session