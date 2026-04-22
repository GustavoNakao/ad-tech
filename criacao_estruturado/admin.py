from django.contrib import admin
from .models import ControleSequencia, NumeroReciclado, HistoricoKit, BaselinkerKitSku


@admin.register(ControleSequencia)
class ControleSequenciaAdmin(admin.ModelAdmin):
    list_display = ('nome', 'proximo_numero')


@admin.register(NumeroReciclado)
class NumeroRecicladoAdmin(admin.ModelAdmin):
    list_display = ('numero',)
    search_fields = ('numero',)


@admin.register(HistoricoKit)
class HistoricoKitAdmin(admin.ModelAdmin):
    list_display = ('sku_kit', 'sku_origem', 'id_origem', 'id_kit', 'criado_em')
    search_fields = ('sku_kit', 'sku_origem')
    list_filter = ('criado_em',)
    ordering = ('-criado_em',)


@admin.register(BaselinkerKitSku)
class BaselinkerKitSkuAdmin(admin.ModelAdmin):
    list_display = ('sku', 'product_id', 'sincronizado_em')
    search_fields = ('sku',)
