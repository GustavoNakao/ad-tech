from django.urls import path
from .views import painel_kits
from .views_titulo_manual import painel_titulo_manual  # TEMPORÁRIO — remover com o arquivo

urlpatterns = [
    # O caminho será: site.com/gerador-kits/
    path('gerador-kits/', painel_kits, name='painel_kits'),
    # TEMPORÁRIO — remover quando não precisar mais
    path('gerador-kits/titulo-manual/', painel_titulo_manual, name='painel_titulo_manual'),
]