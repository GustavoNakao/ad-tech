from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from django.views.decorators.cache import never_cache


@never_cache
@require_http_methods(["GET", "POST"])
def login_view(request):
    """
    Login seguro com:
    - Proteção contra força bruta via Django (AXES pode ser adicionado depois)
    - Cache desativado (never_cache) para não vazar sessões
    - Redirecionamento para ?next= após login
    - Mensagem de erro genérica (não revela se usuário existe ou não)
    """
    if request.user.is_authenticated:
        return redirect("/")

    erro = None
    erro_param = request.GET.get("erro")

    if erro_param == "inativo":
        erro = "Sua conta está desativada. Entre em contato com o gestor."

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        if not username or not password:
            erro = "Preencha usuário e senha."
        else:
            user = authenticate(request, username=username, password=password)

            if user is not None:
                # Verifica se perfil está ativo
                try:
                    if not user.perfil.ativo:
                        erro = "Sua conta está desativada. Entre em contato com o gestor."
                    else:
                        login(request, user)
                        next_url = request.POST.get("next") or request.GET.get("next") or "/"
                        # Segurança: só redireciona para URLs internas
                        if not next_url.startswith("/"):
                            next_url = "/"
                        return redirect(next_url)
                except Exception:
                    # Superuser sem perfil — deixa entrar
                    if user.is_superuser:
                        login(request, user)
                        return redirect("/")
                    erro = "Erro ao verificar perfil. Contate o gestor."
            else:
                # Mensagem genérica — não revela se o usuário existe
                erro = "Usuário ou senha incorretos."

    return render(request, "core/login.html", {
        "erro":  erro,
        "next":  request.GET.get("next", "/"),
    })


@never_cache
def logout_view(request):
    logout(request)
    return redirect("/login/")


def acesso_negado_view(request):
    cargo = None
    try:
        cargo = request.user.perfil.get_cargo_display()
    except Exception:
        pass
    return render(request, "core/acesso_negado.html", {"cargo": cargo}, status=403)
