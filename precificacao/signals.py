from django.db.models.signals import post_save
from django.dispatch import receiver


def _invalidar_cache(sender, **kwargs):
    from precificacao.services.calculadora import Calculadora
    Calculadora.limpar_cache()


def conectar_signals():
    from precificacao.models import RegrasPrecificacao, CategoriaComissao, TabelaFreteMercadoEnvios
    post_save.connect(_invalidar_cache, sender=RegrasPrecificacao,        weak=False)
    post_save.connect(_invalidar_cache, sender=CategoriaComissao,         weak=False)
    post_save.connect(_invalidar_cache, sender=TabelaFreteMercadoEnvios,  weak=False)
