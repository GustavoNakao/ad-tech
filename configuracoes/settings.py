"""
Django settings for Configuracoes project – versão 2025 segura

"""
from pathlib import Path
from decouple import config, Csv
import dj_database_url


# Build paths
BASE_DIR = Path(__file__).resolve().parent.parent

# ====================== SEGURANÇA ======================

SECRET_KEY = config('SECRET_KEY')
DEBUG = config('DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', cast=Csv(), default='localhost')


# ====================== ENV ======================

BASELINKER_TOKEN = config('BASELINKER_TOKEN')

GS1_CLIENT_ID     = config('GS1_CLIENT_ID')
GS1_CLIENT_SECRET = config('GS1_CLIENT_SECRET')
GS1_API_KEY       = config('GS1_API_KEY')
GEMINI_API_KEY    = config('GEMINI_API_KEY')

# ====================== APLICAÇÃO ======================
INSTALLED_APPS = [

    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'categorias.apps.CategoriasConfig',
    'inventarios',
    'criacao_estruturado',
    'core',
    'pedidos',
    'devolucoes',
    'envios',
    'precificacao',
    'produtos',
    'anuncios',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'core.middleware.LoginObrigatorioMiddleware',
]

# ====================== SEGURANÇA HTTPS ======================
# Ativar em produção via variável de ambiente
SECURE_SSL_REDIRECT          = config('SECURE_SSL_REDIRECT', default=False, cast=bool)
SESSION_COOKIE_SECURE        = config('SECURE_COOKIES', default=False, cast=bool)
CSRF_COOKIE_SECURE           = config('SECURE_COOKIES', default=False, cast=bool)
SECURE_HSTS_SECONDS          = config('SECURE_HSTS_SECONDS', default=0, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = config('SECURE_HSTS_INCLUDE_SUBDOMAINS', default=False, cast=bool)
SECURE_BROWSER_XSS_FILTER   = True
SECURE_CONTENT_TYPE_NOSNIFF = True

# SEGURANÇA DE SESSÃO
# Expira a sessão após 30 minutos (1800 segundos)
SESSION_COOKIE_AGE = 1800

# Derruba a sessão imediatamente se o usuário fechar a aba/navegador
SESSION_EXPIRE_AT_BROWSER_CLOSE = True

ROOT_URLCONF = 'configuracoes.urls'
WSGI_APPLICATION = 'configuracoes.wsgi.application'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

# ====================== BANCO DE DADOS ======================
DATABASES = {
    'default': dj_database_url.parse(
        config('DATABASE_URL'),
        conn_max_age=600,
        ssl_require=not DEBUG  # força SSL quando estiver em produção
    )
}

# ====================== VALIDAÇÃO DE SENHA ======================
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ====================== INTERNACIONALIZAÇÃO ======================
LANGUAGE_CODE = 'pt-br'      # mudei pra português (recomendado)
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True

# ====================== ARQUIVOS ESTÁTICOS ======================
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'   # importante para produção

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ====================== LOGGING ======================
# Garante que o diretório de logs exista antes de configurar o handler
import os
os.makedirs(BASE_DIR / 'logs', exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'padrao': {
            'format': '[%(asctime)s] %(levelname)s %(name)s %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'padrao',
        },
        'arquivo': {
            'class': 'logging.FileHandler' if os.name == 'nt' else 'logging.handlers.RotatingFileHandler',
            'filename': BASE_DIR / 'logs' / 'app.log',
            'formatter': 'padrao',
            'encoding': 'utf-8',
            **({} if os.name == 'nt' else {
                'maxBytes': 10485760,  # 10 MB por arquivo
                'backupCount': 5,
            })
        },
    },
    'loggers': {
        # Logs do módulo de criação de kits
        'gerador_kits':         {'level': 'INFO',  'handlers': ['console', 'arquivo'], 'propagate': False},
        'gerador_kits.metrics': {'level': 'INFO',  'handlers': ['console', 'arquivo'], 'propagate': False},
        # Logs de todas as integrações externas (BaseLinker, ML, GS1, Gemini, Groq)
        'integracoes':          {'level': 'DEBUG', 'handlers': ['console', 'arquivo'], 'propagate': False},
        # Erros gerais do Django
        'django':               {'level': 'WARNING', 'handlers': ['console', 'arquivo'], 'propagate': False},
    },
}