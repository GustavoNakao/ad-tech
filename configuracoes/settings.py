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


# ====================== APLICAÇÃO ======================
INSTALLED_APPS = [

    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'baselinker_integracao',
    'categorias.apps.CategoriasConfig',
    'inventarios',
    'criacao_estruturado',
    'core',
    'pedidos',
    'devolucoes',
    'envios',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

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