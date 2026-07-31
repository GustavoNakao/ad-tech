from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView

urlpatterns = [
    path('admin/', admin.site.urls),

    # Redireciona a raiz '/' para '/gerador-kits/'
    path('', RedirectView.as_view(url='/gerador-kits/', permanent=False)),

    # Rotas principais
    path('', include('criacao_estruturado.urls')),

    # App de produtos
    path('produtos/', include('produtos.urls')),

    # Core (login, logout, acesso negado)
    path('', include('core.urls')),
]