from django.contrib import admin
from django.urls import path, include
from django.shortcuts import redirect

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # A sua rota principal que já funcionava antes:
    path('', include('criacao_estruturado.urls')), 
    
    # A NOSSA NOVA ROTA AQUI:
    path('produtos/', include('produtos.urls')),
    
    path("", include("core.urls")), 
    
    path('', lambda request: redirect('painel_kits'), name='home'),
    
    ]