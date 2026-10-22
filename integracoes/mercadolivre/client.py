"""
integracoes/mercadolivre/client.py

Client do Mercado Livre com cascata resiliente de 4 camadas para resolução de category_id.

Camadas (em ordem de prioridade):
    0. GET /items/{wid}  SEM token  → itens públicos, evita 403
    1. GET /items/{wid}  COM token  → itens próprios / parceiros
    2. GET /products/{pid}          → catálogo ML (/p/ URLs)
       2a. category_id direto
       2b. buy_box_winner.category_id
       2c. children_items[0].category_id
       2d. domain_id → domain_discovery
    3. domain_discovery com título  → último recurso

Persistência de tokens:
    - expires_at gravado junto ao token → sem chamada a /users/me para checar validade
    - Renovação automática com margem de 5 min antes do vencimento
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Optional
from urllib.parse import unquote, urlparse

import requests
from decouple import config
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger("integracoes.mercadolivre")

ML_SITE      = "MLB"
ML_API_URL   = "https://api.mercadolibre.com"
ML_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"

# Categoria padrão (Estruturado) — fallback quando nenhuma camada resolve
ML_CATEGORIA_PADRAO = "979589"

# Rate limit conservador: 400 req/min para domain_discovery
RATE_LIMIT_INTERVAL = 60 / 400  # ~0.15s entre chamadas

# Persistência de tokens — mesmo diretório do client
_TOKEN_FILE = os.path.join(os.path.dirname(__file__), "ml_tokens.json")

# User-Agent realista para requests públicos (sem token)
_UA_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class MercadoLivreClient:
    """
    Client do Mercado Livre para resolução de category_id a partir de URL de produto.

    Uso principal no sistema:
        cat_id = MercadoLivreClient().buscar_category_id_por_url(url)

    .env necessário (valores iniciais — após o primeiro refresh ficam no ml_tokens.json):
        ML_CLIENT_ID
        ML_CLIENT_SECRET
        ML_ACCESS_TOKEN
        ML_REFRESH_TOKEN
    """

    TIMEOUT     = 15
    MAX_RETRIES = 2  # reduzido — cascata já tem 4 camadas, não precisa retentar muito

    _ultimo_request: float = 0.0

    def __init__(self) -> None:
        self._client_id     = config("ML_CLIENT_ID",     default=None)
        self._client_secret = config("ML_CLIENT_SECRET", default=None)

        if not self._client_id or not self._client_secret:
            raise ImproperlyConfigured("ML_CLIENT_ID e ML_CLIENT_SECRET são obrigatórios no .env")

        # Tokens: arquivo JSON tem prioridade sobre .env (arquivo é sempre mais recente)
        tk = self._carregar_tokens_persistidos()
        self._access_token  = tk.get("access_token")  or config("ML_ACCESS_TOKEN",  default=None)
        self._refresh_token = tk.get("refresh_token") or config("ML_REFRESH_TOKEN", default=None)
        self._expires_at    = tk.get("expires_at", 0.0)

    # =========================================================================
    # PERSISTÊNCIA DE TOKENS
    # =========================================================================

    @staticmethod
    def _carregar_tokens_persistidos() -> dict:
        try:
            if os.path.exists(_TOKEN_FILE):
                with open(_TOKEN_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as exc:
            logger.warning("Falha ao carregar tokens de %s: %s", _TOKEN_FILE, exc)
        return {}

    def _salvar_tokens(self, access_token: str, refresh_token: str, expires_in: int = 21600) -> None:
        """
        Persiste tokens com expires_at calculado.
        expires_at elimina a necessidade de chamar /users/me para checar validade.
        """
        expires_at = time.time() + expires_in
        try:
            payload = {
                "access_token":  access_token,
                "refresh_token": refresh_token,
                "expires_at":    expires_at,
                "salvo_em":      time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            with open(_TOKEN_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            self._expires_at = expires_at
            logger.info("ML tokens salvos. Expiram em %dh.", expires_in // 3600)
        except Exception as exc:
            logger.error("Falha ao salvar tokens ML: %s", exc)

    def _token_esta_valido(self) -> bool:
        """Checa localmente se o token ainda é válido (com 5 min de margem)."""
        return bool(self._expires_at and time.time() < (self._expires_at - 300))

    # =========================================================================
    # AUTENTICAÇÃO
    # =========================================================================

    def _renovar_token_se_necessario(self) -> None:
        """Renova apenas se o token está vencido ou próximo do vencimento."""
        if self._token_esta_valido():
            restante = int(self._expires_at - time.time())
            logger.debug("Token ML válido. Expira em %dh%dm.", restante // 3600, (restante % 3600) // 60)
            return
        self._renovar_token()

    def _renovar_token(self) -> bool:
        if not self._refresh_token:
            logger.error("ML_REFRESH_TOKEN não configurado. Renovação impossível.")
            return False
        try:
            resp = requests.post(
                ML_TOKEN_URL,
                data={
                    "grant_type":    "refresh_token",
                    "client_id":     self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            resp.raise_for_status()
            tk = resp.json()
            self._access_token  = tk.get("access_token",  self._access_token)
            self._refresh_token = tk.get("refresh_token", self._refresh_token)
            self._salvar_tokens(self._access_token, self._refresh_token, tk.get("expires_in", 21600))
            logger.info("ML token renovado. Expires_in=%s.", tk.get("expires_in"))
            return True
        except Exception as exc:
            logger.error("Falha ao renovar token ML: %s", exc)
            return False

    def _headers_auth(self) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "User-Agent":    _UA_BROWSER,
        }

    def _headers_pub(self) -> dict:
        """Headers SEM Authorization — para itens públicos, evita 403."""
        return {"User-Agent": _UA_BROWSER}

    # =========================================================================
    # HTTP INTERNO
    # =========================================================================

    def _aguardar_rate_limit(self) -> None:
        agora = time.monotonic()
        delta = agora - MercadoLivreClient._ultimo_request
        if delta < RATE_LIMIT_INTERVAL:
            time.sleep(RATE_LIMIT_INTERVAL - delta)
        MercadoLivreClient._ultimo_request = time.monotonic()

    def _get_raw(self, url: str, headers: dict, params: Optional[dict] = None) -> Optional[dict]:
        """
        GET direto com retry simples. Retorna o JSON parseado ou None.
        Não renova token — o caller decide quando usar auth ou não.
        """
        self._aguardar_rate_limit()
        for tentativa in range(1, self.MAX_RETRIES + 1):
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=self.TIMEOUT)

                if resp.status_code == 429:
                    espera = int(resp.headers.get("Retry-After", 15))
                    logger.warning("ML 429 — aguardando %ds...", espera)
                    time.sleep(espera)
                    continue

                if resp.status_code in (200,):
                    return resp.json()

                logger.debug("ML GET %s → %d", url[-60:], resp.status_code)
                return None  # 404, 403 etc — não retentar com mesmo header

            except requests.exceptions.RequestException as exc:
                logger.warning("ML GET tentativa %d/%d falhou: %s", tentativa, self.MAX_RETRIES, exc)
                if tentativa < self.MAX_RETRIES:
                    time.sleep(2 ** tentativa)
        return None

    def _get_auth(self, endpoint: str, params: Optional[dict] = None) -> Optional[dict]:
        """GET autenticado — garante token válido antes de chamar."""
        self._renovar_token_se_necessario()
        url = f"{ML_API_URL}/{endpoint.lstrip('/')}"
        data = self._get_raw(url, self._headers_auth(), params)

        # 401 → tenta renovar uma vez e repete
        if data is None:
            logger.warning("ML auth falhou em '%s' — tentando renovar token...", endpoint)
            if self._renovar_token():
                data = self._get_raw(url, self._headers_auth(), params)
        return data

    def _post_auth(self, endpoint: str, payload: Optional[dict] = None, params: Optional[dict] = None) -> Optional[dict]:
        """POST autenticado — usado em endpoints condicionais e top values do ML."""
        self._renovar_token_se_necessario()
        url = f"{ML_API_URL}/{endpoint.lstrip('/')}"
        self._aguardar_rate_limit()
        for tentativa in range(1, self.MAX_RETRIES + 1):
            try:
                resp = requests.post(url, headers=self._headers_auth(), json=payload, params=params, timeout=self.TIMEOUT)
                if resp.status_code == 429:
                    espera = int(resp.headers.get("Retry-After", 15))
                    logger.warning("ML 429 POST — aguardando %ds...", espera)
                    time.sleep(espera)
                    continue
                if resp.status_code in (200, 201):
                    return resp.json()
                logger.debug("ML POST %s → %d", endpoint[-60:], resp.status_code)
                return None
            except requests.exceptions.RequestException as exc:
                logger.warning("ML POST tentativa %d/%d falhou: %s", tentativa, self.MAX_RETRIES, exc)
                if tentativa < self.MAX_RETRIES:
                    time.sleep(2 ** tentativa)
        return None

    def _get_pub(self, endpoint: str, params: Optional[dict] = None) -> Optional[dict]:
        """GET público SEM Authorization — resolve 403 em itens de terceiros."""
        url = f"{ML_API_URL}/{endpoint.lstrip('/')}"
        return self._get_raw(url, self._headers_pub(), params)

    # =========================================================================
    # EXTRAÇÃO DE IDS DA URL
    # =========================================================================

    @staticmethod
    def _extrair_ids_url(url: str) -> tuple[Optional[str], Optional[str]]:
        """
        Extrai wid (item ID tipo MLB3435415331) e pid (product ID tipo MLB2072269984) da URL.

        Formatos suportados:
            /MLB-3435415331-titulo     → wid = MLB3435415331
            /p/MLB12345678             → pid = MLB12345678
            ?wid=MLB12345678           → wid = MLB12345678
            MLB3435415331 (texto puro) → wid = MLB3435415331
        """
        url_dec = unquote(url)
        parsed  = urlparse(url_dec)
        busca   = f"{parsed.query}&{parsed.fragment}"

        # ?wid= no query string
        wid_match = re.search(r'wid=(MLB\d+)', busca, re.IGNORECASE)
        # /p/MLBXXXXXXX ou /up/MLBXXXXXXX (catálogo)
        pid_match = re.search(r'/[u]?p/(MLB[U]?\d+)', parsed.path, re.IGNORECASE)
        # MLB-XXXXXXX no slug final
        slug      = parsed.path.rstrip('/').split('/')[-1]
        gen_match = re.search(r'MLB[\-]?(\d+)', slug, re.IGNORECASE)

        wid = wid_match.group(1).upper() if wid_match else None
        pid = pid_match.group(1).replace('-', '').upper() if pid_match else None

        if not wid and not pid and gen_match:
            wid = "MLB" + gen_match.group(1)

        return wid, pid

    # =========================================================================
    # HELPERS DO DOMAIN DISCOVERY
    # =========================================================================

    @staticmethod
    def _normalizar_domain_id(domain_id: str) -> str:
        """Remove prefixo do site: 'MLB-WHEY_PROTEIN' → 'WHEY_PROTEIN'."""
        return re.sub(r'^MLB-', '', (domain_id or '').upper()).strip()

    @staticmethod
    def _limpar_termo_predictor(texto: str) -> str:
        """Remove lixo de URL/tracking para usar como query no domain_discovery."""
        if not texto:
            return ""
        texto = texto.split('#')[0]
        texto = re.sub(r'MLB[\s\-]?\d+', ' ', texto, flags=re.IGNORECASE)
        texto = re.sub(r'\b\d{5,}\b', ' ', texto)
        texto = re.sub(
            r'\.html|_JM|\.JM|/p/|produto|mercadolivre|com\.br|\.com|\.br',
            ' ', texto, flags=re.IGNORECASE,
        )
        stopwords = {
            'polycard', 'client', 'reco', 'backend', 'model', 'level', 'ranker', 'compl',
            'marketplace', 'retsys', 'tpt', 'pdp', 'vip', 'tracking', 'storefronts',
            'global', 'position', 'component', 'de', 'da', 'do', 'dos', 'das', 'para',
            'com', 'sem', 'em', 'por', 'e', 'a', 'o', 'os', 'as', 'um', 'uma',
        }
        texto = texto.replace('-', ' ').replace('_', ' ').replace('=', ' ')
        tokens = [t for t in texto.lower().split() if t not in stopwords and len(t) > 1]
        vistos: set = set()
        uniq = [t for t in tokens if not (t in vistos or vistos.add(t))]
        return ' '.join(uniq).strip()

    @staticmethod
    def _limpar_titulo_para_categoria(titulo: str) -> str:
        """Limpa prefixos de SKU e códigos alfanuméricos curtos do título."""
        if not titulo:
            return ""
        titulo = titulo.strip()
        
        while True:
            # Captura prefixos delimitados por -, |, : ou / no início do título
            match = re.match(r'^([^-\|:\/]{1,15})\s*([-\|:\/])\s*(.*)$', titulo)
            if not match:
                break
            prefixo = match.group(1).strip()
            resto = match.group(3).strip()
            
            is_codigo = False
            if prefixo.isdigit():
                is_codigo = True
            elif any(c.isdigit() for c in prefixo) and len(prefixo) <= 8:
                is_codigo = True
            elif len(prefixo) <= 4:
                is_codigo = True
            elif prefixo.upper() in ["COD", "REF", "SKU", "PROD", "MODELO", "MARCA", "FABRICANTE", "CONJUNTO", "KIT"]:
                is_codigo = True
                
            if is_codigo and resto:
                titulo = resto
            else:
                break
                
        # Remove palavras soltas puramente numéricas ou códigos curtos no início
        match_inicio = re.match(r'^(\b\d{1,6}\b|\b[a-zA-Z]\d{1,2}\b)\s+(.*)$', titulo, re.IGNORECASE)
        if match_inicio:
            titulo = match_inicio.group(2).strip()
            
        return titulo

    def _domain_para_category(self, domain_id: str, nome_produto: str = "") -> Optional[str]:
        """
        Converte domain_id em category_id via domain_discovery.
        Primeiro tenta match exato (normalizado), depois usa a primeira sugestão.
        """
        if not domain_id:
            return None

        domain_norm = self._normalizar_domain_id(domain_id)
        termo       = self._limpar_termo_predictor(nome_produto) if nome_produto else domain_norm

        if not termo:
            logger.debug("_domain_para_category: termo vazio para domain '%s'.", domain_id)
            return None

        logger.debug("domain_discovery: domain='%s' | query='%s'", domain_id, termo[:60])
        data = self._get_pub(
            f"sites/{ML_SITE}/domain_discovery/search",
            params={"limit": 5, "q": termo},
        )

        if not data or not isinstance(data, list):
            return None

        # Match exato pelo domain_id normalizado
        for sug in data:
            sug_norm = self._normalizar_domain_id(sug.get('domain_id', ''))
            if sug_norm == domain_norm:
                cat = sug.get('category_id')
                if cat:
                    logger.info("domain→category match exato: %s → %s", domain_id, cat)
                    return cat

        # Fallback: primeira sugestão
        cat = data[0].get('category_id') if data else None
        if cat:
            logger.info("domain→category aproximado: %s → %s", domain_id, cat)
        return cat

    def _preditor_fallback(self, termo: str) -> Optional[str]:
        """Domain_discovery com título do produto como último recurso."""
        termo_limpo = self._limpar_termo_predictor(termo)
        if not termo_limpo:
            return None
        logger.info("Predictor fallback: query='%s'", termo_limpo[:80])
        data = self._get_pub(
            f"sites/{ML_SITE}/domain_discovery/search",
            params={"limit": 3, "q": termo_limpo},
        )
        if data and isinstance(data, list):
            for sug in data:
                cat = sug.get('category_id')
                if cat:
                    logger.info("Predictor fallback → %s", cat)
                    return cat
        return None

    # =========================================================================
    # CASCATA PRINCIPAL — buscar_category_id_por_url
    # =========================================================================

    def buscar_category_id_por_url(
        self,
        url_produto: str,
        fallback_id: str = ML_CATEGORIA_PADRAO,
    ) -> str:
        """
        Resolve o category_id do ML a partir da URL pública de um produto.

        Cascata de 4 camadas:
            0. GET /items/{wid} SEM token       → evita 403 em itens de terceiros
            1. GET /items/{wid} COM token        → itens próprios / sellers parceiros
            2. GET /products/{pid} (catálogo)    → URLs tipo /p/MLB...
               2a. category_id direto
               2b. buy_box_winner.category_id
               2c. children_items[0].category_id
               2d. domain_id → domain_discovery
            3. domain_discovery com título       → último recurso

        Retorna fallback_id (979589 — Estruturado) se nenhuma camada resolver.

        Uso:
            cat_id = MercadoLivreClient().buscar_category_id_por_url(
                "https://www.mercadolivre.com.br/...",
            )
        """
        if not url_produto or not url_produto.strip():
            logger.info("buscar_category_id_por_url: URL vazia → fallback %s.", fallback_id)
            return fallback_id

        wid, pid = self._extrair_ids_url(url_produto)
        logger.info("buscar_category_id_por_url: URL='%s...' | wid=%s | pid=%s",
                    url_produto[:60], wid, pid)

        cat_id: Optional[str] = None
        nome_produto: str = ""

        # ── CAMADA 0: /items/{wid} público (SEM token) ────────────────────────
        if wid and not cat_id:
            logger.info("[Camada 0] GET público /items/%s", wid)
            data = self._get_pub(
                f"items/{wid}",
                params={"attributes": "id,title,category_id,status,domain_id"},
            )
            if data:
                nome_produto = data.get("title", "")
                cat_id       = data.get("category_id") or None

                if cat_id:
                    logger.info("[Camada 0] ✅ category_id=%s (title='%s')", cat_id, nome_produto[:50])
                else:
                    # Tenta domain_id mesmo sem category_id direto
                    domain_0 = data.get("domain_id")
                    if domain_0:
                        cat_id = self._domain_para_category(domain_0, nome_produto)
                        if cat_id:
                            logger.info("[Camada 0-domain] ✅ category_id=%s via domain_id=%s", cat_id, domain_0)
            else:
                logger.info("[Camada 0] Sem resposta pública para %s.", wid)

        # ── CAMADA 1: /items/{wid} autenticado ───────────────────────────────
        if wid and not cat_id:
            logger.info("[Camada 1] GET autenticado /items/%s", wid)
            data = self._get_auth(
                f"items/{wid}",
                params={"attributes": "id,title,category_id,status,domain_id"},
            )
            if data:
                if not nome_produto:
                    nome_produto = data.get("title", "")
                cat_id = data.get("category_id") or None
                if cat_id:
                    logger.info("[Camada 1] ✅ category_id=%s", cat_id)
                else:
                    domain_1 = data.get("domain_id")
                    if domain_1:
                        cat_id = self._domain_para_category(domain_1, nome_produto)
            else:
                logger.info("[Camada 1] Autenticado também falhou para %s.", wid)

        # ── CAMADA 2: /products/{pid} — catálogo ML ──────────────────────────
        if pid and not cat_id:
            logger.info("[Camada 2] GET catálogo /products/%s", pid)
            data = self._get_auth(f"products/{pid}")
            if data:
                if not nome_produto:
                    nome_produto = data.get("name") or data.get("title", "")

                # 2a. category_id direto
                cat_id = data.get("category_id") or None
                if cat_id:
                    logger.info("[Camada 2a] ✅ category_id direto=%s", cat_id)

                # 2b. buy_box_winner
                if not cat_id:
                    bbw = data.get("buy_box_winner")
                    if isinstance(bbw, dict):
                        cat_id = bbw.get("category_id") or None
                        if cat_id:
                            logger.info("[Camada 2b] ✅ via buy_box_winner=%s", cat_id)

                # 2c. children_items
                if not cat_id:
                    children = data.get("children_items") or []
                    if children and isinstance(children[0], dict):
                        cat_id = children[0].get("category_id") or None
                        if cat_id:
                            logger.info("[Camada 2c] ✅ via children_items=%s", cat_id)

                # 2d. domain_id → domain_discovery
                if not cat_id:
                    domain_2 = data.get("domain_id")
                    if domain_2:
                        cat_id = self._domain_para_category(domain_2, nome_produto)
                        if cat_id:
                            logger.info("[Camada 2d] ✅ via domain_id=%s → %s", domain_2, cat_id)
            else:
                logger.info("[Camada 2] Catálogo não respondeu para %s.", pid)

        # ── CAMADA 3: domain_discovery com título (último recurso) ────────────
        if not cat_id and nome_produto:
            logger.info("[Camada 3] Predictor fallback com title='%s'", nome_produto[:60])
            cat_id = self._preditor_fallback(nome_produto)
            if cat_id:
                logger.info("[Camada 3] ✅ category_id=%s via predictor.", cat_id)

        # ── RESULTADO ─────────────────────────────────────────────────────────
        if cat_id:
            logger.info("buscar_category_id_por_url: ✅ Resolvido → %s", cat_id)
            return str(cat_id)

        logger.warning(
            "buscar_category_id_por_url: ❌ Nenhuma camada resolveu para '%s'. Fallback=%s.",
            url_produto[:80], fallback_id,
        )
        return fallback_id

    @staticmethod
    def _remover_acentos_norm_com_espacos(txt: str) -> str:
        """Remove acentos e pontuações preservando espaços para tokenização."""
        if not txt:
            return ""
        import unicodedata
        nfkd = unicodedata.normalize("NFKD", txt)
        ascii_txt = nfkd.encode("ASCII", "ignore").decode("ASCII")
        return re.sub(r'[^a-z0-9\s]', ' ', ascii_txt.lower())

    def resolver_categoria_por_input(self, entrada: str) -> Optional[str]:
        """
        Resolve o category_id do ML a partir de entrada manual do usuário:
        1. URL do produto (http... ou mercadolivre.com...)
        2. ID direto da categoria (MLB277472 ou 277472)
        3. Árvore de categoria digitada/copiada manualmente pelo usuário:
           - Com separadores: "Casa, Móveis e Decoração / Banheiros / Sifões"
           - Sem separadores (copiada direta do ML): "Casa, Móveis e DecoraçãoBanheirosSifões"
        """
        if not entrada or not entrada.strip():
            return None

        texto = entrada.strip()

        # 1. URL do produto
        if "http://" in texto or "https://" in texto or "mercadolivre.com" in texto.lower():
            cat = self.buscar_category_id_por_url(texto)
            if cat and cat != ML_CATEGORIA_PADRAO:
                return cat

        # 2. ID direto de categoria (ex: MLB277472 ou 277472)
        match_id = re.match(r'^(?:MLB)?(\d+)$', texto, re.IGNORECASE)
        if match_id:
            cand_id = f"MLB{match_id.group(1)}"
            det = self.detalhes_categoria(cand_id)
            if det and not det.get("depreciada") and det.get("name"):
                logger.info("Resolver Categoria: ID direto válido '%s' (%s)", cand_id, det.get("name"))
                return cand_id

        # 3. Texto da Árvore de Categorias (Formatos 1 e 2)
        # Separa palavras coladas estilo camelCase/TitleCase (ex: "DecoraçãoBanheiros" -> "Decoração Banheiros")
        texto_separado = re.sub(r'([a-zà-ÿ0-9])([A-ZÀ-ÖØ-ß])', r'\1 \2', texto)
        query_limpa = re.sub(r'[/><,;\-]', ' ', texto_separado)
        query_limpa = re.sub(r'\s+', ' ', query_limpa).strip()

        # Tokens do texto informado pelo usuário
        palavras_input = [w for w in self._remover_acentos_norm_com_espacos(texto_separado).split() if len(w) >= 3]
        set_tokens_input = set(palavras_input)

        logger.info("Resolver Categoria por árvore: texto original='%s' | query='%s'", texto[:60], query_limpa[:60])

        queries_testar = [query_limpa[:100]]
        palavras = query_limpa.split()
        if len(palavras) >= 2:
            queries_testar.append(' '.join(palavras[-2:]))
        if len(palavras) >= 3:
            queries_testar.append(' '.join(palavras[-3:]))
        if len(palavras) >= 1 and palavras[-1] not in queries_testar:
            queries_testar.append(palavras[-1])

        candidatos_vistos: set = set()
        candidates: list = []

        for q_test in queries_testar:
            if not q_test or len(q_test) < 2:
                continue
            res = self._get_pub(
                f"sites/{ML_SITE}/domain_discovery/search",
                params={"q": q_test, "limit": 5},
            )
            if res and isinstance(res, list):
                for item in res:
                    c_id = item.get("category_id")
                    if c_id and c_id not in candidatos_vistos:
                        candidatos_vistos.add(c_id)
                        candidates.append(item)

        if not candidates:
            logger.warning("Resolver Categoria: Nenhuma categoria encontrada para '%s'", texto[:60])
            return None

        melhor_cat_id = None
        melhor_score = -1.0

        for item in candidates:
            cand_id = item.get("category_id")
            if not cand_id:
                continue

            det = self.detalhes_categoria(cand_id)
            if not det or det.get("depreciada"):
                continue

            # Avalia match de níveis de caminho (path)
            path_str = det.get("path", "")
            niveis = [n.strip() for n in path_str.split(">") if n.strip()]

            # 1. Contagem de tokens que coincidem entre o caminho da categoria e o texto informado pelo usuário
            path_tokens = set(w for w in self._remover_acentos_norm_com_espacos(path_str).split() if len(w) >= 3)
            token_overlap = len(path_tokens.intersection(set_tokens_input))

            score = float(token_overlap)

            # 2. Bônus para folha (sem subcategorias filhas)
            if not det.get("children_categories"):
                score += 3.0

            # 3. Bônus se o nome da folha contiver termos finais do input do usuário
            if niveis:
                folha_norm = self._remover_acentos_norm_com_espacos(niveis[-1])
                ultimas = palavras_input[-2:] if len(palavras_input) >= 2 else palavras_input
                if any(p in folha_norm for p in ultimas):
                    score += 5.0

            logger.debug("Candidata %s ('%s') -> score=%.1f", cand_id, path_str, score)

            if score > melhor_score:
                melhor_score = score
                melhor_cat_id = cand_id

        if melhor_cat_id and melhor_score > 0:
            logger.info("Resolver Categoria por árvore: ✅ Resolvido → %s (score=%.1f)", melhor_cat_id, melhor_score)
            return melhor_cat_id

        logger.warning("Resolver Categoria por árvore: ❌ Nenhuma candidata confiável para '%s'", texto[:60])
        return None

    # =========================================================================
    # MÉTODOS DE CATEGORIAS (usados pelo admin Django)
    # =========================================================================

    def predict_categoria(self, titulo: str, limite: int = 3) -> list[dict]:
        """
        Prediz categorias para um título via domain_discovery.
        Usado para prever categorias de novos kits.
        """
        if not titulo or not titulo.strip():
            return []
        
        titulo_limpo = self._limpar_titulo_para_categoria(titulo)
        if not titulo_limpo:
            titulo_limpo = titulo.strip()

        data = self._get_pub(
            f"sites/{ML_SITE}/domain_discovery/search",
            params={"q": titulo_limpo, "limit": limite},
        )
        if not data or not isinstance(data, list):
            return []
        resultados = []
        for i, item in enumerate(data):
            resultados.append({
                "category_id":   item.get("category_id", ""),
                "category_name": item.get("category_name", ""),
                "domain_id":     item.get("domain_id", ""),
                "domain_name":   item.get("domain_name", ""),
                "confiavel":     i == 0,
                "atributos": [
                    {"id": a.get("id", ""), "name": a.get("value_name") or a.get("id", ""), "value_id": a.get("value_id", "")}
                    for a in item.get("attributes", [])
                ],
            })
        # Regra de Negócio de Loja: produtos spray / aerossol pertencem a MLB271283 (Tintas Sprays)
        if any(term in titulo.lower() for term in ["spray", "aerossol"]):
            idx_spray = next((i for i, r in enumerate(resultados) if r.get("category_id") == "MLB271283"), None)
            if idx_spray is not None and idx_spray != 0:
                item_spray = resultados.pop(idx_spray)
                resultados.insert(0, item_spray)
            elif not any(r.get("category_id") == "MLB271283" for r in resultados):
                resultados.insert(0, {
                    "category_id": "MLB271283",
                    "category_name": "Tintas Sprays",
                    "domain_id": "PAINTS_AND_VARNISHES",
                    "domain_name": "Tintas e Vernizes",
                    "confiavel": True,
                    "atributos": []
                })

        if titulo != titulo_limpo:
            logger.info("predict_categoria '%s' (limpo: '%s') → %d resultado(s). Top Cat=%s", titulo[:50], titulo_limpo[:50], len(resultados), resultados[0]["category_id"] if resultados else "None")
        else:
            logger.info("predict_categoria '%s' → %d resultado(s). Top Cat=%s", titulo[:50], len(resultados), resultados[0]["category_id"] if resultados else "None")
        return resultados

    def detalhes_categoria(self, category_id: str) -> Optional[dict]:
        """Detalhes de uma categoria. Marca como inválida no DB se 404."""
        if not category_id:
            return None
        data = self._get_auth(f"categories/{category_id}")
        if data is None:
            return {"id": category_id, "name": "", "path": "", "atributos_obrigatorios": [], "children_categories": [], "depreciada": True}
        path = " > ".join(p.get("name", "") for p in data.get("path_from_root", []) if p.get("name"))
        return {
            "id":                     data.get("id", category_id),
            "name":                   data.get("name", ""),
            "path":                   path,
            "children_categories":    [{"id": c.get("id"), "name": c.get("name")} for c in data.get("children_categories", [])],
            "depreciada":             False,
        }


    def ficha_tecnica(self, category_id: str, apenas_obrigatorios: bool = False) -> list[dict]:
        """
        Consulta os campos da ficha técnica da categoria via Endpoint moderno technical_specs/input.
        Retorna atributos obrigatórios e principais recomendados organizados.
        """
        # --- 1. Nova Ficha Técnica (technical_specs/input) ---
        data = self._get_auth(f"categories/{category_id}/technical_specs/input")
        if data and 'groups' in data:
            candidatos = []
            vistos: set = set()
            for group in data.get('groups', []):
                for component in group.get('components', []):
                    for attr in component.get('attributes', []):
                        field_id   = attr.get("id")
                        field_name = attr.get("name") or component.get("label") or field_id
                        if not field_id or field_id in vistos:
                            continue

                        tags = attr.get('tags', [])

                        # Tags de Obrigatoriedade
                        is_req = False
                        if isinstance(tags, list):
                            if any(t in tags for t in ['required', 'new_required', 'conditional_required', 'catalog_required']):
                                is_req = True
                        elif isinstance(tags, dict):
                            if any(tags.get(t) for t in ['required', 'new_required', 'conditional_required', 'catalog_required']):
                                is_req = True

                        if apenas_obrigatorios and not is_req:
                            continue

                        vistos.add(field_id)
                        candidatos.append({
                            "id": field_id,
                            "name": field_name,
                            "required": is_req,
                            "values": [v.get("name") for v in attr.get("values", []) if v.get("name")]
                        })

            if candidatos:
                candidatos.sort(key=lambda x: not x["required"])
                return candidatos[:25]

        # --- 2. Fallback: Atributos Clássicos (attributes) ---
        data_alt = self._get_auth(f"categories/{category_id}/attributes")
        if data_alt and isinstance(data_alt, list):
            res = []
            for a in data_alt:
                tags = a.get("tags", {})

                is_req = False
                if isinstance(tags, dict):
                    if any(tags.get(t) for t in ["required", "new_required", "catalog_required", "conditional_required"]):
                        is_req = True
                elif isinstance(tags, list):
                    if any(t in tags for t in ["required", "new_required", "catalog_required", "conditional_required"]):
                        is_req = True

                if apenas_obrigatorios and not is_req:
                    continue

                res.append({
                    "id": a.get("id"),
                    "name": a.get("name"),
                    "required": is_req,
                    "values": [v.get("name") for v in a.get("values", []) if v.get("name")]
                })
            res.sort(key=lambda x: not x["required"])
            return res[:25]

        return []

    def technical_specs_input(self, category_id: str, apenas_obrigatorios: bool = False) -> list[dict]:
        """Busca atributos da ficha técnica da categoria via /technical_specs/input."""
        data = self._get_auth(f"categories/{category_id}/technical_specs/input")
        if not data:
            return []

        attrs: list[dict] = []
        if isinstance(data, dict) and 'groups' in data:
            vistos: set = set()
            for group in data.get('groups', []):
                for component in group.get('components', []):
                    for attr in component.get('attributes', []):
                        field_id = attr.get('id')
                        field_name = attr.get('name') or component.get('label') or field_id
                        if not field_id or field_id in vistos:
                            continue
                        tags = attr.get('tags', [])
                        is_req = False
                        if isinstance(tags, list):
                            if any(t in tags for t in ['required', 'new_required', 'conditional_required', 'catalog_required']):
                                is_req = True
                        elif isinstance(tags, dict):
                            if any(tags.get(t) for t in ['required', 'new_required', 'conditional_required', 'catalog_required']):
                                is_req = True
                        if apenas_obrigatorios and not is_req:
                            continue
                        vistos.add(field_id)
                        attrs.append({
                            'id': field_id,
                            'name': field_name,
                            'required': is_req,
                            'values': [v.get('name') for v in attr.get('values', []) if v.get('name')],
                        })
            return attrs

        if isinstance(data, list):
            return [
                {
                    'id': item.get('id'),
                    'name': item.get('name'),
                    'required': bool(item.get('required')),
                    'values': [v.get('name') for v in item.get('values', []) if v.get('name')],
                }
                for item in data if item.get('id')
            ]
        return []

    def atributos_condicionais(self, category_id: str, payload: Optional[dict] = None) -> list[dict]:
        """Consulta atributos condicionalmente obrigatórios em /categories/{id}/attributes/conditional."""
        if not category_id:
            return []

        site = str(ML_SITE or '').upper()
        if site not in {'MLA', 'MLB', 'MLM'}:
            return []

        data = self._post_auth(f"categories/{category_id}/attributes/conditional", payload or {})
        if not data:
            return []

        required_attrs = data.get('required_attributes') or data.get('requiredAttributes') or []
        if not isinstance(required_attrs, list):
            return []

        return [
            {
                'id': item.get('id'),
                'name': item.get('name') or item.get('id'),
                'required': True,
                'values': item.get('values') or [],
            }
            for item in required_attrs if item.get('id')
        ]

    def top_values(self, domain_id: str, attribute_id: str) -> list[dict]:
        """Busca valores mais usados para um atributo em um domínio via /catalog_domains/{domain_id}/attributes/{attribute_id}/top_values."""
        if not domain_id or not attribute_id:
            return []
        data = self._post_auth(f"catalog_domains/{domain_id}/attributes/{attribute_id}/top_values", payload={})
        if not data:
            return []
        if isinstance(data, list):
            return data
        return data.get('values') or data.get('results') or []

    def buscar_categorias(self, query: str, limite: int = 5) -> list[dict]:
        """Autocomplete de categorias para o Admin via domain_discovery."""
        resultados = self.predict_categoria(query, limite=limite)
        vistos: set = set()
        categorias  = []
        for item in resultados:
            cat_id = item.get("category_id", "")
            if cat_id and cat_id not in vistos:
                vistos.add(cat_id)
                categorias.append({"id": cat_id, "name": item.get("category_name", ""), "domain_id": item.get("domain_id", ""), "domain_name": item.get("domain_name", "")})
        return categorias

    def listar_categorias_raiz(self) -> list[dict]:
        """Lista todas as categorias raiz do MLB."""
        data = self._get_pub(f"sites/{ML_SITE}/categories")
        if not data or not isinstance(data, list):
            return []
        return [{"id": c.get("id", ""), "name": c.get("name", "")} for c in data if c.get("id")]

    # =========================================================================
    # MONITORAMENTO DE COMPLIANCE (MODERAÇÕES E PPPI)
    # =========================================================================

    def obter_meu_id(self) -> Optional[int]:
        """Retorna o ID do usuário autenticado (seller)."""
        data = self._get_auth("users/me")
        return data.get("id") if data else None

    def listar_itens_moderados(self) -> list[str]:
        """Lista IDs de itens que sofreram algum tipo de moderação recente."""
        user_id = self.obter_meu_id()
        if not user_id: return []
        
        data = self._get_auth(f"users/{user_id}/items/search", params={"tags": "moderation_penalty", "status": "paused"})
        return data.get("results", []) if data else []

    def obter_detalhe_moderacao(self, item_id: str) -> Optional[dict]:
        """Consulta o motivo detalhado da última moderação de um item."""
        return self._get_auth(f"moderations/last_moderation/{item_id}")

    def listar_casos_bpp(self, status: str = "") -> list[dict]:
        """
        Lista casos de denúncia PPPI (BPP - Brand Protection Program).
        Status: 'active', 'pending', 'closed'
        """
        params = {"status": status} if status else {}
        data = self._get_auth("moderations/pppi/cases", params=params)
        return data.get("results", []) if data else []

    def detalhar_caso_bpp(self, case_id: str) -> Optional[dict]:
        """Consulta os detalhes de uma denúncia específica de marca."""
        return self._get_auth(f"moderations/pppi/case/{case_id}")

    # =========================================================================
    # CATÁLOGO OFICIAL DO ML (GET /products/search) — FONTE PRIMÁRIA DA FICHA TÉCNICA
    # =========================================================================

    def buscar_atributos_catalogo_ml(self, ean: str = "", termo_busca: str = "", marca: str = "", category_id: str = "") -> dict:
        """
        Busca o produto oficial no Catálogo do Mercado Livre (GET /products/search).
        
        Se category_id for informado (categoria definida pelo usuário no app), a busca é 
        restrita estritamente aos produtos de catálogo dentro daquela categoria.
        """
        queries_tentar = []
        
        # 1. Busca por EAN (código de barras original do fabricante) se legítimo
        ean_limpo = str(ean).strip()
        if ean_limpo and len(ean_limpo) >= 12 and ean_limpo.isdigit():
            queries_tentar.append(ean_limpo)

        # 2. Busca por nome limpo do produto base (sem prefixos SKU/Kit N) + marca
        if termo_busca:
            nome_limpo = re.sub(r'^[\d\s\|\-]+', '', termo_busca).strip()
            nome_limpo = re.sub(r'^Kit\s+\d+\s+', '', nome_limpo, flags=re.IGNORECASE).strip()
            nome_limpo = re.sub(r'/\s*\d+\s*[gGkK]', '', nome_limpo).strip()
            
            marca_invalida = any(m in marca.lower() for m in ["marca não informada", "não informado", "nao informado", "n/a", "sem marca", "indefinido"])
            termo_marca = f" {marca.strip()}" if marca and not marca_invalida and marca.lower() not in nome_limpo.lower() else ""
            if nome_limpo:
                queries_tentar.append(f"{nome_limpo}{termo_marca}".strip())

        atributos_catalogo = {}

        for q in queries_tentar:
            if not q:
                continue
                
            params = {"site_id": "MLB", "status": "active", "q": q}
            if category_id and category_id != "979589":
                params["category_id"] = str(category_id).strip()

            logger.info("[Catálogo ML] Buscando produto de catálogo (Cat: %s) para query: '%s'", params.get("category_id", "Todas"), q[:60])
            res = self._get_auth("products/search", params=params)
            
            if res and isinstance(res, dict) and res.get("results"):
                for prod in res["results"]:
                    raw_attrs = prod.get("attributes", [])
                    if not raw_attrs:
                        continue
                    
                    prod_name = (prod.get("name") or prod.get("title") or "").lower()
                    if marca and marca.lower() not in prod_name:
                        cand_brand = next((a.get("value_name") for a in raw_attrs if a.get("id") == "BRAND"), "")
                        if marca.lower() not in cand_brand.lower():
                            continue

                    MAPA_SINONIMOS = {
                        "volume da unidade": "Volume líquido",
                        "peso da unidade": "Peso líquido",
                    }
                    for a in raw_attrs:
                        val = a.get("value_name") or a.get("value_id")
                        name = a.get("name")
                        if name and val and str(val).lower() not in ("não informado", "null", "n/a", "preencher", ""):
                            # Mapeia sinônimos para os nomes canônicos da categoria ML
                            name_canonico = MAPA_SINONIMOS.get(name.strip().lower(), name)
                            if name_canonico.lower() not in ("unidades por kit", "quantidade", "formato de venda", "código universal de produto", "gtin", "ean"):
                                atributos_catalogo[name_canonico] = str(val)

                    if atributos_catalogo:
                        return atributos_catalogo

        return atributos_catalogo

    def obter_atributos_categoria_ml(self, category_id: str) -> List[dict]:
        """
        Retorna a lista completa de especificações/atributos da categoria na API do ML
        (com tags, valores permitidos, ids e hierarquia).
        Cache de 24 horas (86400s) por category_id.
        """
        cat_clean = str(category_id or "").strip()
        if not cat_clean or cat_clean == "979589":
            return []

        cache_key = f"ml_category_attributes_v2_{cat_clean}"
        cached = cache.get(cache_key)
        if cached is not None and isinstance(cached, list):
            return cached

        endpoint = f"categories/{cat_clean}/attributes"
        logger.info("[ML Category Attributes] Consultando API pública de atributos da categoria '%s'", cat_clean)

        res = self._get_pub(endpoint)
        if res and isinstance(res, list):
            cache.set(cache_key, res, timeout=86400)
            return res

        return []


