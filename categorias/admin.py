from django.contrib import admin
from django.utils.html import format_html
from django.contrib import messages
from django.utils import timezone

from categorias.models.categoria_baselinker import CategoriaBaselinker
from categorias.models.termos_proibidos import TermosProibidos
from categorias.models.historico_ia import HistoricoIA


# ── CategoriaBaselinker ───────────────────────────────────────────────────────

@admin.register(CategoriaBaselinker)
class CategoriaBaselinkerAdmin(admin.ModelAdmin):
    list_display  = ("name", "category_id", "parent", "inventory_id", "ativo", "sincronizado_em")
    list_filter   = ("ativo",)
    search_fields = ("name", "category_id")
    ordering      = ["name"]
    readonly_fields = ("criado_em", "atualizado_em", "sincronizado_em")




# ── TermosProibidos ───────────────────────────────────────────────────────────

@admin.register(TermosProibidos)
class TermosProibidosAdmin(admin.ModelAdmin):
    list_display  = ("padrao_regex", "categoria", "descricao_curta", "badge_ativo", "atualizado_em")
    list_filter   = ("categoria", "ativo")
    search_fields = ("padrao_regex", "descricao")
    ordering      = ["categoria", "padrao_regex"]

    fieldsets = (
        ("🚫 Termo Proibido", {
            "fields": ("padrao_regex", "categoria", "descricao", "ativo"),
            "description": (
                "Use word boundary \\\\b para evitar falsos positivos. "
                "Ex: \\\\bfake\\\\b bloqueia 'fake' mas não 'safekeeping'. "
                "O sistema usa re.IGNORECASE automaticamente."
            ),
        }),
    )

    @admin.display(description="Descrição")
    def descricao_curta(self, obj):
        return obj.descricao[:60] + "…" if len(obj.descricao) > 60 else obj.descricao

    @admin.display(description="Ativo")
    def badge_ativo(self, obj):
        if obj.ativo:
            return format_html('<span style="color:green;font-weight:600;">✅ Ativo</span>')
        return format_html('<span style="color:gray;">⏸ Inativo</span>')

    actions = ["ativar_termos", "desativar_termos", "testar_regex"]

    @admin.action(description="✅ Ativar termos selecionados")
    def ativar_termos(self, request, queryset):
        total = queryset.update(ativo=True)
        self.message_user(request, f"✅ {total} termo(s) ativado(s).", messages.SUCCESS)

    @admin.action(description="⏸ Desativar termos selecionados")
    def desativar_termos(self, request, queryset):
        total = queryset.update(ativo=False)
        self.message_user(request, f"⏸ {total} termo(s) desativado(s).", messages.WARNING)

    @admin.action(description="🧪 Testar regex (valida sintaxe)")
    def testar_regex(self, request, queryset):
        import re
        ok = err = 0
        for termo in queryset:
            try:
                re.compile(termo.padrao_regex)
                ok += 1
            except re.error as e:
                self.message_user(request, f"❌ Regex inválida '{termo.padrao_regex}': {e}", messages.ERROR)
                err += 1
        if ok:
            self.message_user(request, f"✅ {ok} regex(s) válida(s).", messages.SUCCESS)


# ── HistoricoIA ───────────────────────────────────────────────────────────────

@admin.register(HistoricoIA)
class HistoricoIAAdmin(admin.ModelAdmin):
    list_display = (
        "item_id_ml", "titulo_curto", "badge_status",
        "domain_id_ml", "score_formatado_col",
        "foi_para_rascunho", "revisado_por", "data_processamento",
    )
    list_filter  = ("status", "foi_para_rascunho", "violacao_politica_ml")
    search_fields = ("item_id_ml", "titulo_original", "url_original", "sku_gerado")
    ordering      = ["-data_processamento"]
    readonly_fields = (
        "data_processamento", "atualizado_em",
        "payload_ia_cru_fmt", "payload_pos_revisao_fmt",
        "score_barra",
    )

    fieldsets = (
        ("📥 Entrada", {
            "fields": ("url_original", "item_id_ml", "titulo_original", ("category_id_ml", "domain_id_ml")),
        }),
        ("🚫 Compliance", {
            "fields": ("termo_bloqueado", "violacao_politica_ml"),
        }),
        ("🤖 Resultado da IA", {
            "fields": ("payload_ia_cru_fmt", "score_barra"),
        }),
        ("👁 Revisão Humana", {
            "fields": ("payload_pos_revisao_fmt", "revisado_por", "revisado_em"),
            "classes": ("collapse",),
        }),
        ("📦 Destino BaseLinker", {
            "fields": (
                "category_id_baselinker", "sku_gerado", "product_id_baselinker",
                ("foi_para_rascunho", "motivo_rascunho"),
            ),
        }),
        ("⚙️ Status", {
            "fields": ("status", "erro_detalhe"),
        }),
        ("🕐 Auditoria", {
            "fields": ("data_processamento", "atualizado_em"),
            "classes": ("collapse",),
        }),
    )

    @admin.display(description="Título")
    def titulo_curto(self, obj):
        t = obj.titulo_original or obj.url_original
        return t[:50] + "…" if len(t) > 50 else t

    @admin.display(description="Status")
    def badge_status(self, obj):
        cores = {
            "PENDENTE":   ("#888",    "⏳"),
            "QUARENTENA": ("#ff4d6d", "🚫"),
            "REVISAO":    ("#ffc14d", "👁"),
            "FALHA_IA":   ("#ff4d6d", "❌"),
            "FALHA_ML":   ("#ff4d6d", "❌"),
            "FALHA_BL":   ("#ff4d6d", "❌"),
            "CONCLUIDO":  ("#00e599", "✅"),
        }
        cor, emoji = cores.get(obj.status, ("#888", "?"))
        return format_html(
            '<span style="color:{};font-weight:600;">{} {}</span>',
            cor, emoji, obj.get_status_display(),
        )

    @admin.display(description="Score")
    def score_formatado_col(self, obj):
        if obj.score_ia is None:
            return format_html('<span style="color:#888">—</span>')
        pct = float(obj.score_ia) * 100
        cor = "#00e599" if pct >= 80 else "#ffc14d" if pct >= 50 else "#ff4d6d"
        return format_html('<span style="color:{};font-weight:600;">{:.0f}%</span>', cor, pct)

    @admin.display(description="Score (barra)")
    def score_barra(self, obj):
        if obj.score_ia is None:
            return "—"
        pct = float(obj.score_ia) * 100
        cor = "#00e599" if pct >= 80 else "#ffc14d" if pct >= 50 else "#ff4d6d"
        return format_html(
            '<div style="background:#1a1a2e;border-radius:6px;height:16px;width:200px;">'
            '<div style="background:{};width:{}%;height:100%;border-radius:6px;"></div>'
            '</div>'
            '<small style="color:{}">{:.0f}% ({}/{})</small>',
            cor, min(pct, 100), cor, pct,
            obj.atributos_corretos, obj.atributos_total,
        )

    @admin.display(description="Payload IA Bruto")
    def payload_ia_cru_fmt(self, obj):
        if not obj.payload_ia_cru:
            return "—"
        import json
        txt = json.dumps(obj.payload_ia_cru, ensure_ascii=False, indent=2)
        return format_html('<pre style="font-size:11px;max-height:300px;overflow:auto;">{}</pre>', txt)

    @admin.display(description="Payload Pós-Revisão")
    def payload_pos_revisao_fmt(self, obj):
        if not obj.payload_pos_revisao:
            return "— (não revisado)"
        import json
        txt = json.dumps(obj.payload_pos_revisao, ensure_ascii=False, indent=2)
        return format_html('<pre style="font-size:11px;max-height:300px;overflow:auto;">{}</pre>', txt)

    actions = ["marcar_revisado", "reprocessar_pendentes"]

    @admin.action(description="✅ Marcar como Concluído (revisão manual)")
    def marcar_revisado(self, request, queryset):
        total = queryset.update(
            status=HistoricoIA.Status.CONCLUIDO,
            revisado_por=request.user,
            revisado_em=timezone.now(),
        )
        self.message_user(request, f"✅ {total} item(s) marcado(s) como concluído.", messages.SUCCESS)

    @admin.action(description="🔄 Resetar para Pendente (reprocessar)")
    def reprocessar_pendentes(self, request, queryset):
        total = queryset.update(
            status=HistoricoIA.Status.PENDENTE,
            erro_detalhe="",
            score_ia=None,
            payload_ia_cru=None,
        )
        self.message_user(request, f"🔄 {total} item(s) resetado(s) para reprocessar.", messages.SUCCESS)
