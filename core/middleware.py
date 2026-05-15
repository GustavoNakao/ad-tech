"""
core/middleware.py

Protege todas as rotas do sistema.
Qualquer URL que não seja login/logout/admin redireciona para o login
se o usuário não estiver autenticado.

Também bloqueia usuários com perfil inativo.
"""
from django.shortcuts import redirect
from django.urls import reverse


# Rotas que não precisam de autenticação
ROTAS_PUBLICAS = [
    "/login/",
    "/logout/",
    "/admin/",
]


class LoginObrigatorioMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path

        # Permite rotas públicas e admin
        if any(path.startswith(rota) for rota in ROTAS_PUBLICAS):
            return self.get_response(request)

        # Redireciona para login se não autenticado
        if not request.user.is_authenticated:
            return redirect(f"/login/?next={path}")

        # Bloqueia usuário com perfil inativo
        try:
            if not request.user.perfil.ativo:
                from django.contrib.auth import logout
                logout(request)
                return redirect("/login/?erro=inativo")
        except Exception:
            pass  # superuser pode não ter perfil

        return self.get_response(request)
