from django.urls import path
from . import views

urlpatterns = [
    path("login/",         views.login_view,        name="login"),
    path("logout/",        views.logout_view,        name="logout"),
    path("acesso-negado/", views.acesso_negado_view, name="acesso_negado"),
]
