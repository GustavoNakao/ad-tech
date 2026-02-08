# categorias/apps.py
from django.apps import AppConfig

class CategoriasConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'categorias'           # ← tem que ser exatamente o nome da pasta
    verbose_name = "Categorias"