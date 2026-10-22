"""
integracoes/groq/client.py

Client do Groq para modelos Llama com alta velocidade.
Suporta rotação de chaves e tratamento de rate limits.

Atualizações:
- 2026-09-16: llama-3.1-8b-instant deprecado pela Groq (16/08/2026).
  Substituído por llama-3.2-3b-preview para SEO/TECH.
- 2026-09-16: Adicionado close_old_connections() para evitar
  InterfaceError após chamadas longas à Groq que fecham a conexão DB.
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Dict, List, Optional

import requests
from decouple import config
from django.core.cache import cache
from django.db import close_old_connections

logger = logging.getLogger(__name__)

class GroqAPIError(Exception):
    """Erro genérico da API Groq."""
    pass

class GroqRateLimitError(GroqAPIError):
    """Todas as chaves esgotadas após retries."""
    pass

class _KeyPool:
    def __init__(self, chaves: List[str]) -> None:
        self._chaves = list(chaves)
        self._idx = 0
        self._ultima_chamada: Dict[str, float] = {}

    def _disponivel(self, chave: str) -> bool:
        key_hash = hashlib.md5(chave.encode()).hexdigest()
        return cache.get(f"groq_cooldown_{key_hash}") is None

    def proxima(self) -> str:
        n = len(self._chaves)
        for _ in range(n):
            chave = self._chaves[self._idx % n]
            self._idx += 1
            if self._disponivel(chave):
                return chave
        raise GroqRateLimitError("Todas as chaves Groq estão em cooldown.")

    def marcar_429(self, chave: str, seconds: int = 60) -> None:
        key_hash = hashlib.md5(chave.encode()).hexdigest()
        cache.set(f"groq_cooldown_{key_hash}", "bloqueado", timeout=seconds)

_pool: Optional[_KeyPool] = None

def _get_pool() -> _KeyPool:
    global _pool
    if _pool is None:
        chaves = []
        unica = config("GROQ_API_KEY", default=None)
        if unica: chaves.append(unica.strip())
        for i in range(1, 6):
            c = config(f"GROQ_API_KEY_{i}", default=None)
            if c: chaves.append(c.strip())
        _pool = _KeyPool(chaves)
    return _pool

class GroqClient:
    BASE_URL = "https://api.groq.com/openai/v1"

    # Modelos ativos na Groq (atualizado 2026-09-16)
    # llama-3.1-8b-instant foi deprecado em 16/08/2026
    MODELO_SEO   = "llama-3.2-3b-preview"     # leve/rápido para SEO
    MODELO_COPY  = "llama-3.3-70b-versatile"  # robusto para copywriting
    MODELO_TECH  = "llama-3.2-3b-preview"     # leve/rápido para atributos técnicos

    TIMEOUT = 30
    MAX_TENTATIVAS = 5

    def chat(self, system_prompt: str, user_input: str, modelo: str = "llama-3.2-3b-preview", temperature: float = 0.0) -> Optional[str]:
        pool = _get_pool()
        for tentativa in range(self.MAX_TENTATIVAS):
            try:
                chave = pool.proxima()
                url = f"{self.BASE_URL}/chat/completions"
                headers = {
                    "Authorization": f"Bearer {chave}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": modelo,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_input}
                    ],
                    "temperature": temperature,
                    "response_format": {"type": "json_object"} if "json" in system_prompt.lower() else None
                }
                
                logger.info(f"🚀 Groq: Chamando modelo {modelo}...")
                start_time = time.time()
                resp = requests.post(url, json=payload, headers=headers, timeout=self.TIMEOUT)
                
                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After", "desconhecido")
                    logger.warning(f"⚠️ Groq 429: Chave em cooldown. Retry-After: {retry_after}")
                    pool.marcar_429(chave)
                    continue
                
                resp.raise_for_status()
                data = resp.json()
                duration = time.time() - start_time
                logger.info(f"✅ Groq: Sucesso em {duration:.2f}s.")
                # Força o Django a reabrir conexões DB que possam ter
                # sido fechadas durante a chamada bloqueante à Groq.
                close_old_connections()
                return data["choices"][0]["message"]["content"]

            except GroqRateLimitError:
                logger.error("Groq: Rate limit total.")
                close_old_connections()
                return None
            except Exception as exc:
                logger.error(f"Groq Erro: {exc}")
                if tentativa == self.MAX_TENTATIVAS - 1:
                    close_old_connections()
                    return None
                time.sleep(1)
        close_old_connections()
        return None
