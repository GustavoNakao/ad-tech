from django.urls import path
from .views import painel_kits

urlpatterns = [
    # O caminho será: site.com/gerador-kits/
    path('gerador-kits/', painel_kits, name='painel_kits'),
]