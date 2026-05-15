from django.contrib import admin
from django.utils.html import format_html
from django.contrib import messages

from precificacao.models.regras import (
    RegrasPrecificacao,
    TabelaFreteMercadoEnvios,
    CategoriaComissao,
)
from precificacao.services.calculadora import Calculadora


# ── RegrasPrecificacao ────────────────────────────────────────────────────────

@admin.register(RegrasPrecificacao)
class RegrasPrecificacaoAdmin(admin.ModelAdmin):
    list_display = (
        "nome", "badge_ativo", "reputacao_conta",
        "margem_minima_pct", "trava_fiscal_pct",
        "comissao_classico_fallback_pct", "desconto_vitrine_pct",
        "custo_baselinker", "custo_gnre", "atualizado_em",
    )
    list_filter  = ("ativo", "reputacao_conta")
    readonly_fields = ("criado_em", "atualizado_em", "preview_divisor", "preview_grade_exemplo")

    fieldsets = (
        ("📋 Identificação", {
            "fields": ("nome", "ativo"),
        }),
        ("🏆 Reputação da Conta", {
            "fields": ("reputacao_conta",),
            "description": "MercadoLíder Verde aplica 50% de desconto na tabela de frete do Mercado Envios.",
        }),
        ("📈 Denominador de Blindagem", {
            "fields": (
                ("margem_minima", "trava_fiscal"),
                ("comissao_classico_fallback", "comissao_premium_fallback"),
                "preview_divisor",
            ),
            "description": "Denominador = 1 − (margem + trava_fiscal + comissão). Deve ser > 0.",
        }),
        ("🎯 Tarifa Reduzida (R$150–R$700)", {
            "fields": (
                ("comissao_classico_reduzida", "comissao_premium_reduzida"),
                ("tarifa_reduzida_min", "tarifa_reduzida_max"),
            ),
            "classes": ("collapse",),
        }),
        ("💰 Custos Operacionais (Numerador)", {
            "fields": (("custo_baselinker", "custo_gnre"),),
            "description": "Somados ao custo do produto antes da divisão.",
        }),
        ("🚚 Piso e Taxa Fixa ML", {
            "fields": (
                ("piso_frete_gratis", "taxa_fixa_ml"),
                ("piso_faixa_min", "piso_faixa_max", "piso_valor_forcado"),
            ),
        }),
        ("🏷️ Preço de Vitrine", {
            "fields": (
                ("desconto_vitrine", "margem_maxima_vitrine"),
            ),
            "description": "Vitrine = Promoção ÷ (1 − desconto_vitrine). Teto = custo × (1 + margem_maxima_vitrine).",
        }),
        ("⚖️ Peso e Terminação", {
            "fields": ("peso_max_mercado_envios", "terminacao_preco"),
        }),
        ("🧪 Preview Grade Exemplo", {
            "fields": ("preview_grade_exemplo",),
            "classes": ("collapse",),
            "description": "Simula um produto de custo R$30,00 com a configuração atual.",
        }),
        ("🕐 Auditoria", {
            "fields": ("criado_em", "atualizado_em"),
            "classes": ("collapse",),
        }),
    )

    # ── Colunas ───────────────────────────────────────────────────────────────

    @admin.display(description="Status", ordering="ativo")
    def badge_ativo(self, obj):
        if obj.ativo:
            return format_html('<span style="color:green;font-weight:bold;">✅ ATIVA</span>')
        return format_html('<span style="color:gray;">⏸ Inativa</span>')

    @admin.display(description="Margem Mín.")
    def margem_minima_pct(self, obj):
        return f"{float(obj.margem_minima):.1%}"

    @admin.display(description="Trava Fiscal")
    def trava_fiscal_pct(self, obj):
        return f"{float(obj.trava_fiscal):.1%}"

    @admin.display(description="Comissão Clás.")
    def comissao_classico_fallback_pct(self, obj):
        return f"{float(obj.comissao_classico_fallback):.1%}"

    @admin.display(description="Desconto Vitrine")
    def desconto_vitrine_pct(self, obj):
        return f"{float(obj.desconto_vitrine):.1%}"

    @admin.display(description="Divisor (preview)")
    def preview_divisor(self, obj):
        comissao  = float(obj.comissao_classico_fallback)
        trava     = float(obj.trava_fiscal)
        margem    = float(obj.margem_minima)
        divisor   = 1.0 - comissao - trava - margem

        if divisor <= 0:
            return format_html(
                '<span style="color:red;font-weight:bold;">❌ DIVISOR INVÁLIDO ({:.4f})</span>',
                divisor,
            )
        exemplo   = round(30.0 / divisor, 2)
        return format_html(
            '✅ Divisor = <b>{:.4f}</b> | Custo R$30 → Promoção <b>R${:.2f}</b>',
            divisor, exemplo,
        )

    @admin.display(description="Grade Exemplo (custo R$30)")
    def preview_grade_exemplo(self, obj):
        try:
            Calculadora.limpar_cache()
            dados   = {"average_cost": 30.0, "weight": 0.350, "tax_rate": 9.25}
            grade   = Calculadora.calcular_grade(dados, sku_ref="PREVIEW")

            if grade.bloqueado:
                return format_html('<span style="color:red;">🔴 {}</span>', grade.motivo_bloqueio)

            linhas = []
            for a in grade.anuncios:
                tar  = " 🎯" if a.tarifa_reduzida_aplicada else ""
                cor  = "green" if not a.bloqueado else "red"
                linhas.append(
                    f'<tr style="color:{cor}">'
                    f'<td>{a.referencia}</td>'
                    f'<td>R${a.preco_promocao:.2f}</td>'
                    f'<td>R${a.preco_vitrine:.2f}{tar}</td>'
                    f'<td>R${a.custo_logistico:.2f}</td>'
                    f'</tr>'
                )

            return format_html(
                '<table style="font-size:11px;border-collapse:collapse;">'
                '<tr><th>Ref</th><th>Promoção</th><th>Vitrine</th><th>Frete</th></tr>'
                '{}</table>',
                "".join(linhas),
            )
        except Exception as exc:
            return format_html('<span style="color:red;">Erro: {}</span>', str(exc))

    # ── Ações ─────────────────────────────────────────────────────────────────

    actions = ["ativar_configuracao"]

    @admin.action(description="✅ Ativar esta configuração")
    def ativar_configuracao(self, request, queryset):
        if queryset.count() > 1:
            self.message_user(request, "⚠️ Selecione apenas uma configuração.", messages.WARNING)
            return
        RegrasPrecificacao.objects.update(ativo=False)
        queryset.update(ativo=True)
        Calculadora.limpar_cache()
        self.message_user(request, "✅ Configuração ativada!", messages.SUCCESS)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        Calculadora.limpar_cache()
        self.message_user(
            request,
            "✅ Regras salvas! Cache do motor invalidado automaticamente.",
            messages.SUCCESS,
        )


# ── TabelaFreteMercadoEnvios ──────────────────────────────────────────────────

@admin.register(TabelaFreteMercadoEnvios)
class TabelaFreteAdmin(admin.ModelAdmin):
    list_display = ("peso_min_kg", "peso_max_kg", "custo_tabela", "custo_com_desconto", "ativo")
    list_filter  = ("ativo",)
    ordering     = ["peso_min_kg"]

    @admin.display(description="Com 50% Desconto (MercadoLíder)")
    def custo_com_desconto(self, obj):
        return f"R${float(obj.custo_tabela) * 0.50:.2f}"

    actions = ["ativar_faixas", "desativar_faixas"]

    @admin.action(description="✅ Ativar faixas")
    def ativar_faixas(self, request, queryset):
        total = queryset.update(ativo=True)
        self.message_user(request, f"✅ {total} faixa(s) ativada(s).", messages.SUCCESS)

    @admin.action(description="❌ Desativar faixas")
    def desativar_faixas(self, request, queryset):
        total = queryset.update(ativo=False)
        self.message_user(request, f"❌ {total} faixa(s) desativada(s).", messages.WARNING)


# ── CategoriaComissao ─────────────────────────────────────────────────────────

@admin.register(CategoriaComissao)
class CategoriaComissaoAdmin(admin.ModelAdmin):
    list_display = (
        "category_id", "nome_categoria",
        "comissao_classico_pct", "comissao_premium_pct",
        "badge_tarifa_reduzida", "ativo", "atualizado_em",
    )
    list_filter  = ("ativo", "elegivel_tarifa_reduzida")
    search_fields = ("category_id", "nome_categoria")
    ordering     = ["nome_categoria"]

    @admin.display(description="Clássico")
    def comissao_classico_pct(self, obj):
        return f"{float(obj.comissao_classico):.1%}"

    @admin.display(description="Premium")
    def comissao_premium_pct(self, obj):
        return f"{float(obj.comissao_premium):.1%}"

    @admin.display(description="Tar. Reduzida")
    def badge_tarifa_reduzida(self, obj):
        if obj.elegivel_tarifa_reduzida:
            return format_html('<span style="color:#1a73e8;font-weight:600;">🎯 Sim</span>')
        return format_html('<span style="color:gray;">—</span>')

    actions = ["habilitar_tarifa_reduzida", "desabilitar_tarifa_reduzida"]

    @admin.action(description="🎯 Habilitar Tarifa Reduzida")
    def habilitar_tarifa_reduzida(self, request, queryset):
        total = queryset.update(elegivel_tarifa_reduzida=True)
        Calculadora.limpar_cache()
        self.message_user(request, f"🎯 {total} categoria(s) com tarifa reduzida habilitada.", messages.SUCCESS)

    @admin.action(description="❌ Desabilitar Tarifa Reduzida")
    def desabilitar_tarifa_reduzida(self, request, queryset):
        total = queryset.update(elegivel_tarifa_reduzida=False)
        Calculadora.limpar_cache()
        self.message_user(request, f"❌ {total} categoria(s) sem tarifa reduzida.", messages.WARNING)
