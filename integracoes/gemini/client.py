import logging
import time
from typing import Any, Dict, Optional

import requests
from decouple import config
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


class GeminiAPIError(Exception):
    """Erro específico da API Gemini"""
    pass


class GeminiClient:
    """
    Cliente da API Google Gemini (AI Studio)
    Autenticação via x-goog-api-key no header.
    Documentação: https://ai.google.dev/api
    """

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
    DEFAULT_MODEL = "gemini-2.5-flash"
    TIMEOUT = 30

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or config("GEMINI_API_KEY", default=None)

        if not self._api_key:
            raise ImproperlyConfigured("GEMINI_API_KEY não configurado no .env / settings.")

    # ------------------------------------------------------------------
    # Sessão base
    # ------------------------------------------------------------------

    def _session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update({
            "x-goog-api-key": self._api_key,
            "Content-Type": "application/json",
            "User-Agent": "MeuSistema-Django/1.0",
        })
        return session

    # ------------------------------------------------------------------
    # Request base
    # ------------------------------------------------------------------

    def _post(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        import time # Apenas garantindo que o módulo de tempo está disponível
        url = f"{self.BASE_URL}/{endpoint}"
        session = self._session()
        
        MAX_TENTATIVAS = 3

        for tentativa in range(1, MAX_TENTATIVAS + 1):
            try:
                response = session.post(url, json=payload, timeout=self.TIMEOUT)
                
                # Regra de sobrevivência: Se bater no limite da cota (429), espere!
                if response.status_code == 429:
                    tempo_espera = 5 * tentativa # Espera 5s, depois 10s...
                    logger.warning("⏳ Limite de API (429) atingido. Esperando %ss (Tentativa %s/%s)...", tempo_espera, tentativa, MAX_TENTATIVAS)
                    time.sleep(tempo_espera)
                    continue # Volta para o início do loop e tenta de novo

                # Se for outro erro (400, 500), ele lança a exceção normal
                response.raise_for_status()
                
                # Se passou sem erros, quebra o loop e segue a vida
                break 

            except requests.exceptions.RequestException as e:
                if tentativa == MAX_TENTATIVAS:
                    logger.error("❌ Erro de rede fatal na API Gemini [%s] após %s tentativas: %s", endpoint, MAX_TENTATIVAS, e)
                    raise
                logger.warning("⚠️ Oscilação de rede na tentativa %s. Retentando em 2s...", tentativa)
                time.sleep(2)

        data = response.json()

        if "error" in data:
            error = data["error"]
            logger.warning("Gemini API Error [%s]: %s", endpoint, error.get("message"))
            raise GeminiAPIError(f"Gemini API Error {error.get('code')}: {error.get('message')}")

        return data

    # ------------------------------------------------------------------
    # Teste de conexão
    # ------------------------------------------------------------------

    def test_connection(self) -> bool:
        """
        Valida se a API key está funcionando com uma chamada mínima.
        Retorna True se OK, False caso contrário.
        """
        try:
            self.generate("Olá")
            return True
        except Exception as e:
            logger.error("Falha no teste de conexão Gemini: %s", e)
            return False

    # ------------------------------------------------------------------
    # Método principal (usado internamente e por futuros métodos)
    # ------------------------------------------------------------------

    def generate(self, prompt: str, model: Optional[str] = None) -> str:
        """
        Envia um prompt e retorna o texto da resposta.
        Base para todos os outros métodos que vierem depois.
        """
        model = model or self.DEFAULT_MODEL
        endpoint = f"models/{model}:generateContent"

        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt}],
                    "role": "user",
                }
            ]
        }

        data = self._post(endpoint, payload)

        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            logger.error("Resposta inesperada da API Gemini: %s", data)
            raise GeminiAPIError(f"Resposta inesperada da API Gemini: {e}") from e
        
