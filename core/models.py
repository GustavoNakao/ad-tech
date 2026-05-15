from django.db import models
from django.contrib.auth.models import User


class PerfilUsuario(models.Model):
    """
    Estende o User padrão do Django com cargo/nível de acesso.
    Criado automaticamente via signal ao criar um novo User.
    """

    class Cargo(models.TextChoices):
        GESTOR   = "gestor",   "🔑 Gestor — acesso total"
        OPERADOR = "operador", "📋 Operador — produtos e fila"
        CADASTRO = "cadastro", "🎨 Cadastro — painel de kits"

    usuario = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="perfil",
        verbose_name="Usuário",
    )
    cargo = models.CharField(
        max_length=20,
        choices=Cargo.choices,
        default=Cargo.CADASTRO,
        verbose_name="Cargo",
    )
    ativo = models.BooleanField(
        default=True,
        verbose_name="Ativo",
        help_text="Desativar bloqueia o acesso sem deletar o usuário.",
    )
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = "Perfil de Usuário"
        verbose_name_plural = "Perfis de Usuários"

    def __str__(self):
        return f"{self.usuario.username} [{self.get_cargo_display()}]"

    @property
    def e_gestor(self):
        return self.cargo == self.Cargo.GESTOR

    @property
    def e_operador(self):
        return self.cargo in (self.Cargo.GESTOR, self.Cargo.OPERADOR)

    @property
    def e_cadastro(self):
        return True  # todos os cargos acessam o painel de kits
