"""
core/decorators.py

Decoradores de controle de acesso por cargo.

Uso:
    from core.decorators import gestor_required, operador_required

    @gestor_required
    def minha_view(request): ...

    @operador_required
    def outra_view(request): ...
"""
from functools import wraps
from django.shortcuts import redirect


def _get_cargo(request):
    try:
        return request.user.perfil.cargo
    except Exception:
        return None


def gestor_required(view_func):
    """Apenas Gestores."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_superuser:
            return view_func(request, *args, **kwargs)
        cargo = _get_cargo(request)
        if cargo == "gestor":
            return view_func(request, *args, **kwargs)
        return redirect("/acesso-negado/")
    return wrapper


def operador_required(view_func):
    """Gestores e Operadores."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_superuser:
            return view_func(request, *args, **kwargs)
        cargo = _get_cargo(request)
        if cargo in ("gestor", "operador"):
            return view_func(request, *args, **kwargs)
        return redirect("/acesso-negado/")
    return wrapper


def cadastro_required(view_func):
    """Todos os cargos autenticados (gestor, operador, cadastro)."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated:
            return view_func(request, *args, **kwargs)
        return redirect("/login/")
    return wrapper
