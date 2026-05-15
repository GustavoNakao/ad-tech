"""
core/signals.py

Cria o PerfilUsuario automaticamente sempre que um User é criado.
Isso garante que ninguém fique sem perfil — inclusive usuários criados via Admin.
"""
from django.db.models.signals import post_save
from django.contrib.auth.models import User
from django.dispatch import receiver


@receiver(post_save, sender=User)
def criar_perfil(sender, instance, created, **kwargs):
    if created:
        from core.models import PerfilUsuario
        PerfilUsuario.objects.get_or_create(usuario=instance)
