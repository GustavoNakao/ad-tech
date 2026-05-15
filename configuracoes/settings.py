"""
Django settings for Configuracoes project – versão 2025 segura

"""
from pathlib import Path
from decouple import config, Csv
import dj_database_url
from decouple import config

BASELINK_TOKEN = config('BASELINKER_TOKEN')

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

# ====================== LOGGING (SKU Kits) ======================
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'gerador_kits': {
            'format': '[%(asctime)s] %(levelname)s %(name)s %(message)s',
            'datefmt': '%Y-%m-%d %H:%M:%S',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'gerador_kits',
        },
    },
    'loggers': {
        'gerador_kits': {'level': 'INFO', 'handlers': ['console']},
        'gerador_kits.metrics': {'level': 'INFO', 'handlers': ['console']},
    },
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "loggers": {
        "integracoes": {
            "handlers": ["console"],
            "level": "DEBUG",
        },
    },
}