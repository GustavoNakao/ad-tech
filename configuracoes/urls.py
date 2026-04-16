from django.contrib import admin
from django.urls import path, include  # <--- ADICIONE O 'include' AQUI

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # Adicione esta linha para conectar seu app:
    path('', include('criacao_estruturado.urls')), 
]