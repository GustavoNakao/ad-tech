"""
integracoes/gemini/client.py

Client do Google Gemini com rotação automática de chaves API.

Melhorias implementadas:
    1. Múltiplas chaves — GEMINI_API_KEY_1..N com round-robin automático
    2. Rotação no 429 — chave esgotada vai para o fim da fila, próxima assume
    3. systemInstruction nativo — instrução separada do conteúdo do usuário
    4. Fallback de modelos — flash-2.5 → flash-1.5 → flash-8b
    5. Rate limit por chave — cada chave tem seu próprio cooldown, não bloqueia as demais
    6. Cache Django 24h — mesmo prompt não chama a API duas vezes

.env:
    # Opção A — múltiplas chaves (recomendado)
    GEMINI_API_KEY_1=AIza...
    GEMINI_API_KEY_2=AIza...
    GEMINI_API_KEY_3=AIza...

    # Opção B — chave única (compatibilidade com configuração anterior)
    GEMINI_API_KEY=AIza...
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Dict, List, Optional

import requests
from decouple import config
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


class GeminiAPIError(Exception):
    """Erro genérico da API Gemini."""
    pass


class GeminiRateLimitError(GeminiAPIError):
    """Todas as chaves esgotadas após retries."""
    pass


# =============================================================================
# GERENCIADOR DE CHAVES — round-robin com cooldown por chave
# =============================================================================

class _KeyPool:
    """
    Pool de chaves API com round-robin e cooldown individual.

    Quando uma chave recebe 429, ela entra em cooldown (padrão 60s).
    As demais chaves continuam sendo usadas normalmente.
    Só levanta GeminiRateLimitError quando TODAS as chaves estão em cooldown.
    """

    COOLDOWN_429 = 65  # segundos de cooldown após 429 (margem acima do minuto do ML)

    def __init__(self, chaves: List[str]) -> None:
        if not chaves:
            raise ImproperlyConfigured(
                "Nenhuma GEMINI_API_KEY encontrada no .env. "
                "Defina GEMINI_API_KEY ou GEMINI_API_KEY_1, GEMINI_API_KEY_2, ..."
            )
        self._chaves         = list(chaves)
        self._idx            = 0                        # próxima chave a usar
        self._ultima_chamada: Dict[str, float] = {}    # chave → último uso (em memória)

    def _disponivel(self, chave: str) -> bool:
        # Usa o cache do Django para persistência (sobrevive a restarts)
        key_hash = hashlib.md5(chave.encode()).hexdigest()
        return cache.get(f"gemini_cooldown_{key_hash}") is None

    def proxima(self) -> str:
        """
        Retorna a próxima chave disponível em round-robin.
        Levanta GeminiRateLimitError se todas estiverem em cooldown.
        """
        n = len(self._chaves)
        for _ in range(n):
            chave = self._chaves[self._idx % n]
            self._idx += 1
            if self._disponivel(chave):
                return chave

        # Todas em cooldown — como estamos usando cache, simplificamos a mensagem
        raise GeminiRateLimitError(
            f"Todas as {n} chave(s) Gemini estão em cooldown no cache. "
            f"Aguarde o tempo de expiração (padrão 65s) para tentar novamente."
        )

    def marcar_429(self, chave: str) -> None:
        """Coloca a chave em cooldown usando o Cache do Django."""
        key_hash = hashlib.md5(chave.encode()).hexdigest()
        cache.set(f"gemini_cooldown_{key_hash}", "bloqueado", timeout=self.COOLDOWN_429)
        logger.warning(
            "Gemini chave ...%s marcada em COOLDOWN no cache por %ds.",
            chave[-6:], self.COOLDOWN_429,
        )

    def registrar_uso(self, chave: str) -> None:
        self._ultima_chamada[chave] = time.monotonic()

    def aguardar_intervalo(self, chave: str, intervalo_min: float) -> None:
        """Respeita o intervalo mínimo por chave com um pequeno jitter."""
        import random
        ultima = self._ultima_chamada.get(chave, 0.0)
        delta  = time.monotonic() - ultima
        
        # Adiciona jitter de 0 a 1s para evitar sincronia
        jitter = random.uniform(0, 1.0)
        espera_base = intervalo_min - delta
        
        if espera_base > 0 or jitter > 0:
            total = max(0, espera_base) + jitter
            logger.debug("Gemini ...%s: aguardando %.2fs (base=%.1f + jitter=%.1f).", 
                         chave[-6:], total, espera_base, jitter)
            time.sleep(total)

    def status(self) -> Dict[str, Any]:
        """Diagnóstico — quantas chaves disponíveis agora."""
        disponiveis = 0
        for c in self._chaves:
            if self._disponivel(c):
                disponiveis += 1
        
        return {
            "total":       len(self._chaves),
            "disponiveis": disponiveis,
            "em_cooldown": len(self._chaves) - disponiveis,
        }


def _carregar_chaves() -> List[str]:
    """
    Carrega chaves do .env de forma exaustiva.
    Busca GEMINI_API_KEY e também GEMINI_API_KEY_1 até GEMINI_API_KEY_10.
    """
    chaves: List[str] = []
    
    # 1. Tenta a chave padrão (sem número)
    unica = config("GEMINI_API_KEY", default=None)
    if unica and unica.strip():
        chaves.append(unica.strip())

    # 2. Tenta chaves numeradas de 1 a 10 (exaustivo)
    for i in range(1, 11):
        chave = config(f"GEMINI_API_KEY_{i}", default=None)
        if chave:
            chave = chave.strip()
            if chave and chave not in chaves:
                chaves.append(chave)

    logger.info("Gemini: %d chave(s) carregada(s) do .env.", len(chaves))
    return chaves


# Pool global compartilhado por todas as instâncias no processo
_pool: Optional[_KeyPool] = None


def _get_pool() -> _KeyPool:
    global _pool
    if _pool is None:
        _pool = _KeyPool(_carregar_chaves())
    return _pool


# =============================================================================
# CLIENTE PRINCIPAL
# =============================================================================

class GeminiClient:
    """
    Client do Google Gemini com rotação automática de chaves.

    Uso:
        client = GeminiClient()
        texto = client.chat(system_prompt="...", user_input="...")

    O método .chat() usa systemInstruction nativo da API — instrução e
    conteúdo do usuário são enviados em campos separados, melhorando
    a qualidade das respostas SEO.
    """

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

    # Modelos em ordem de preferência — fallback automático se o principal falhar
    MODELOS = [
        "gemini-2.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-2.0-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash-lite",
    ]

    TIMEOUT       = 45
    MAX_TENTATIVAS = 8  # Aumentado para suportar o backoff exponencial

    # Intervalo mínimo por chave (não global) — 15 RPM → ~4s por chave
    # Com 2 chaves: efetivo ~2s. Com 3 chaves: ~1.3s.
    MIN_INTERVALO_S = 4.0

    # Backoff no 429 antes de rotacionar chave
    BACKOFF_BASE = 5
    BACKOFF_MAX  = 30

    # Cache Django — 24h para respostas SEO
    CACHE_TTL = 60 * 60 * 24

    def __init__(self) -> None:
        # Valida que existe ao menos uma chave — levanta ImproperlyConfigured se não
        _get_pool()

    # =========================================================================
    # INTERFACE PÚBLICA
    # =========================================================================

    def chat(
        self,
        system_prompt: str,
        user_input:    str,
        modelo:        Optional[str] = None,
        use_cache:     bool = True,
    ) -> Optional[str]:
        """
        Envia system_prompt + user_input usando systemInstruction nativo.

        Retorna None em caso de falha para não bloquear o SEOGenerator
        (que usa o nome/descrição original como fallback).

        Exemplo:
            texto = client.chat(
                system_prompt="Você é um especialista em SEO...",
                user_input="Produto: Garrafa Térmica 500ml",
            )
        """
        try:
            return self._gerar_com_rotacao(
                system_prompt=system_prompt.strip(),
                user_input=user_input.strip(),
                modelo_preferido=modelo,
                use_cache=use_cache,
            )
        except GeminiRateLimitError as exc:
            logger.warning("⚠️ Gemini: todas as chaves esgotadas — fallback. %s", exc)
            return None
        except GeminiAPIError as exc:
            logger.warning("⚠️ Gemini API erro — fallback: %s", exc)
            return None
        except Exception as exc:
            logger.error("❌ Gemini erro inesperado — fallback: %s", exc)
            return None

    def generate(
        self,
        prompt:    str,
        use_cache: bool = True,
    ) -> str:
        """
        Interface simples de prompt único (compatibilidade com código legado).
        Usa o prompt inteiro como user_input sem system_prompt.
        """
        return self._gerar_com_rotacao(
            system_prompt="",
            user_input=prompt,
            use_cache=use_cache,
        )

    def test_connection(self) -> bool:
        """Testa se ao menos uma chave está funcionando."""
        try:
            self.generate("Responda apenas: ok", use_cache=False)
            return True
        except Exception as exc:
            logger.error("Falha no teste de conexão Gemini: %s", exc)
            return False

    def status_pool(self) -> Dict[str, Any]:
        """Retorna diagnóstico do pool de chaves (para admin/debug)."""
        return _get_pool().status()

    # =========================================================================
    # NÚCLEO — rotação de chaves + fallback de modelos
    # =========================================================================

    def _gerar_com_rotacao(
        self,
        system_prompt: str,
        user_input:    str,
        modelo_preferido: Optional[str] = None,
        use_cache:     bool = True,
    ) -> str:
        """
        Tenta gerar texto rodando pelo pool de chaves.

        Fluxo por tentativa:
            1. Pega a próxima chave disponível (round-robin)
            2. Respeita o intervalo mínimo daquela chave
            3. Chama a API com backoff por modelo
            4. Se 429 → marca chave em cooldown → passa para a próxima
            5. Se erro de modelo → tenta o próximo modelo da lista
            6. Se todas as chaves em cooldown → levanta GeminiRateLimitError
        """
        pool = _get_pool()

        # Cache — baseado em system+user+modelo para não misturar contextos
        cache_key = self._cache_key(system_prompt, user_input, modelo_preferido) if use_cache else None
        if cache_key:
            cached = cache.get(cache_key)
            if cached:
                logger.debug("✅ Gemini cache hit.")
                return cached

        erros: List[str] = []

        # Define a fila de modelos: modelo_preferido (se houver) + lista padrão
        fila_modelos = list(self.MODELOS)
        if modelo_preferido and modelo_preferido not in fila_modelos:
            fila_modelos.insert(0, modelo_preferido)
        elif modelo_preferido:
            # Move para o topo se já existir
            fila_modelos.remove(modelo_preferido)
            fila_modelos.insert(0, modelo_preferido)

        for tentativa in range(1, self.MAX_TENTATIVAS + 1):
            chave = pool.proxima()  # levanta GeminiRateLimitError se todas em cooldown
            pool.aguardar_intervalo(chave, self.MIN_INTERVALO_S)

            for modelo in fila_modelos:
                try:
                    pool.registrar_uso(chave)
                    texto = self._chamar_api(chave, modelo, system_prompt, user_input)

                    # Sucesso — salva no cache e retorna
                    if cache_key and texto:
                        cache.set(cache_key, texto, timeout=self.CACHE_TTL)
                    logger.info(
                        "✅ Gemini: chave ...%s | modelo=%s | tentativa=%d.",
                        chave[-6:], modelo, tentativa,
                    )
                    return texto

                except _Erro429:
                    # Cálculo de espera exponencial: 10s, 20s, 40s...
                    espera_429 = 10 * (2 ** (tentativa - 1))
                    logger.warning(
                        "Gemini 429: chave ...%s | modelo=%s. Aguardando %ds (Backoff Exponencial).",
                        chave[-6:], modelo, espera_429,
                    )
                    pool.marcar_429(chave)
                    erros.append(f"chave ...{chave[-6:]} 429")
                    
                    # Espera obrigatória para limpar o "burst" e respeitar a cota do projeto
                    time.sleep(espera_429)
                    break  # troca de chave, mantém o modelo

                except _ErroModelo as exc:
                    logger.warning(
                        "Gemini modelo '%s' indisponível: %s. Tentando próximo.",
                        modelo, exc,
                    )
                    erros.append(f"modelo {modelo}: {exc}")
                    continue  # tenta o próximo modelo com a mesma chave

                except GeminiAPIError:
                    raise  # erro de resposta — não adianta rotacionar

        raise GeminiRateLimitError(
            f"Gemini falhou após {self.MAX_TENTATIVAS} tentativas. Erros: {erros}"
        )

    def _chamar_api(
        self,
        chave:         str,
        modelo:        str,
        system_prompt: str,
        user_input:    str,
    ) -> str:
        """
        Faz a chamada HTTP para a API Gemini.

        Usa systemInstruction nativo quando há system_prompt — melhora
        significativamente a qualidade das respostas SEO comparado à
        concatenação manual no campo user.
        """
        url     = f"{self.BASE_URL}/models/{modelo}:generateContent"
        payload = self._montar_payload(system_prompt, user_input)
        session = self._build_session(chave)

        try:
            resp = session.post(url, json=payload, timeout=self.TIMEOUT)
        except requests.exceptions.RequestException as exc:
            raise _ErroModelo(f"Falha de rede/timeout: {exc}")

        if resp.status_code == 429:
            raise _Erro429()

        if resp.status_code in (404, 400) or resp.status_code >= 500:
            # Modelo não existe, indisponível, ou erro interno do Google (ex: 503)
            raise _ErroModelo(f"HTTP {resp.status_code} para modelo {modelo}")

        try:
            resp.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            raise GeminiAPIError(f"HTTP Error não tratado: {exc}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise _ErroModelo(f"Resposta inválida (não-JSON): {exc}")

        if "error" in data:
            err = data["error"]
            code = err.get("code", 0)
            msg  = err.get("message", "")
            if code == 429:
                raise _Erro429()
            if code in (404, 400) or code >= 500 or "not found" in msg.lower() or "deprecated" in msg.lower():
                raise _ErroModelo(msg)
            raise GeminiAPIError(f"Gemini API Error {code}: {msg}")

        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise GeminiAPIError(f"Resposta inesperada da API Gemini: {exc}") from exc

    # =========================================================================
    # HELPERS
    # =========================================================================

    @staticmethod
    def _montar_payload(system_prompt: str, user_input: str) -> Dict[str, Any]:
        """
        Monta o payload com systemInstruction e suporte a JSON Mode.
        """
        payload: Dict[str, Any] = {
            "contents": [
                {"role": "user", "parts": [{"text": user_input}]}
            ],
            "generationConfig": {
                "temperature":     0.7,
                "topP":            0.9,
                "maxOutputTokens": 8192,
            },
            "safetySettings": [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ],
        }
        
        # Ativa JSON Mode se o prompt sugerir
        if "json" in (system_prompt + user_input).lower():
            payload["generationConfig"]["response_mime_type"] = "application/json"

        if system_prompt:
            payload["systemInstruction"] = {
                "parts": [{"text": system_prompt}]
            }
        return payload

    @staticmethod
    def _build_session(chave: str) -> requests.Session:
        session = requests.Session()
        session.headers.update({
            "x-goog-api-key": chave,
            "Content-Type":   "application/json",
            "User-Agent":     "SoProduto-Django/1.0",
        })
        return session

    @staticmethod
    def _cache_key(system_prompt: str, user_input: str, modelo: Optional[str] = None) -> str:
        conteudo = f"sys:{system_prompt}|usr:{user_input}|mod:{modelo or 'default'}"
        h = hashlib.sha256(conteudo.encode()).hexdigest()
        return f"gemini_v2_{h}"


# =============================================================================
# EXCEÇÕES INTERNAS (não exportadas)
# =============================================================================

class _Erro429(Exception):
    """429 recebido — sinaliza rotação de chave."""
    pass


class _ErroModelo(Exception):
    """Modelo indisponível — sinaliza fallback para próximo modelo."""
    pass
