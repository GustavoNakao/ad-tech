from django.urls import path
from . import views

urlpatterns = [
    path('gerador-ean/', views.gerador_ean_view, name='gerador_ean'),
]