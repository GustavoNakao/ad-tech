# core/ — Sistema de Autenticação R831

## Instalação (3 passos)

### 1. settings.py

```python
INSTALLED_APPS = [
    ...
    "core",  # adicionar
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "core.middleware.LoginObrigatorioMiddleware",  # ← adicionar logo após SecurityMiddleware
    ...
]

# Login
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"
```

### 2. urls.py principal

```python
from django.urls import path, include

urlpatterns = [
    path("", include("core.urls")),  # adicionar
    path("admin/", admin.site.urls),
    # ... resto das urls
]
```

### 3. Terminal

```bash
python manage.py migrate core
python manage.py runserver
```

---

## Níveis de Acesso

| Cargo    | Painel de Kits | Fila de Anúncios | Produtos | Regras de Preço | Admin |
|----------|:--------------:|:----------------:|:--------:|:---------------:|:-----:|
| Gestor   | ✅ | ✅ | ✅ | ✅ | ✅ |
| Operador | ✅ | ✅ | ✅ | ❌ | ❌ |
| Cadastro | ✅ | ❌ | ❌ | ❌ | ❌ |

---

## Protegendo views com decoradores

```python
from core.decorators import gestor_required, operador_required, cadastro_required

# Só gestor
@gestor_required
def regras_precificacao(request): ...

# Gestor e operador
@operador_required
def fila_anuncios(request): ...

# Todos (qualquer cargo logado)
@cadastro_required
def painel_kits(request): ...
```

---

## Criar usuário pelo Admin

1. Acesse `/admin/`
2. Vá em **Usuários → Adicionar**
3. Preencha usuário e senha
4. No campo **Perfil de Acesso** escolha o cargo
5. Salvar

O perfil é criado automaticamente via signal — não precisa criar separado.

---

## Segurança implementada

- ✅ Todas as rotas protegidas pelo middleware (sem esquecer nenhuma view)
- ✅ Mensagem de erro genérica no login (não revela se o usuário existe)
- ✅ Cache desativado na tela de login (`never_cache`)
- ✅ Redirecionamento `?next=` apenas para URLs internas
- ✅ Usuário inativo é deslogado automaticamente pelo middleware
- ✅ CSRF token em todos os formulários
