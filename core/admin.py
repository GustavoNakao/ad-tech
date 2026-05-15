from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from django.utils.html import format_html
from .models import PerfilUsuario


class PerfilInline(admin.StackedInline):
    model       = PerfilUsuario
    can_delete  = False
    verbose_name = "Perfil de Acesso"
    fields      = ("cargo", "ativo")


class UserAdmin(BaseUserAdmin):
    inlines = (PerfilInline,)
    list_display = ("username", "email", "first_name", "badge_cargo", "badge_ativo", "is_superuser")

    @admin.display(description="Cargo")
    def badge_cargo(self, obj):
        try:
            cores = {
                "gestor":   ("#1a73e8", "🔑 Gestor"),
                "operador": ("#7b2ff7", "📋 Operador"),
                "cadastro": ("#00a86b", "🎨 Cadastro"),
            }
            cor, label = cores.get(obj.perfil.cargo, ("gray", "—"))
            return format_html('<span style="color:{};font-weight:600;">{}</span>', cor, label)
        except Exception:
            return format_html('<span style="color:gray;">— Sem perfil</span>')

    @admin.display(description="Ativo")
    def badge_ativo(self, obj):
        try:
            if obj.perfil.ativo:
                return format_html('<span style="color:green;">✅ Sim</span>')
            return format_html('<span style="color:red;">🔴 Bloqueado</span>')
        except Exception:
            return "—"


# Substitui o UserAdmin padrão pelo nosso com o perfil inline
admin.site.unregister(User)
admin.site.register(User, UserAdmin)


@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(admin.ModelAdmin):
    list_display  = ("usuario", "badge_cargo", "ativo", "criado_em")
    list_filter   = ("cargo", "ativo")
    search_fields = ("usuario__username", "usuario__email")
    readonly_fields = ("criado_em",)

    @admin.display(description="Cargo")
    def badge_cargo(self, obj):
        cores = {
            "gestor":   ("#1a73e8", "🔑 Gestor"),
            "operador": ("#7b2ff7", "📋 Operador"),
            "cadastro": ("#00a86b", "🎨 Cadastro"),
        }
        cor, label = cores.get(obj.cargo, ("gray", obj.cargo))
        return format_html('<span style="color:{};font-weight:600;">{}</span>', cor, label)

    actions = ["ativar_usuarios", "desativar_usuarios"]

    @admin.action(description="✅ Ativar usuários selecionados")
    def ativar_usuarios(self, request, queryset):
        total = queryset.update(ativo=True)
        self.message_user(request, f"✅ {total} usuário(s) ativado(s).")

    @admin.action(description="🔴 Desativar usuários selecionados")
    def desativar_usuarios(self, request, queryset):
        total = queryset.update(ativo=False)
        self.message_user(request, f"🔴 {total} usuário(s) desativado(s).")
