from django.contrib import admin
from django.utils.html import format_html
from django.contrib import messages
from django.utils import timezone

from anuncios.models import FilaAnuncio
from anuncios.services.processador_fila import ProcessadorFila


@admin.register(FilaAnuncio)
class FilaAnuncioAdmin(admin.ModelAdmin):
    list_display = (
        "id", "badge_tipo", "sku_principal", "descricao_kit_display",
        "badge_status_margem", "badge_status_publicacao",
        "preco_concorrente", "preco_sugerido_display", "margem_real_display",
        "criado_em",
    )
    list_filter  = ("tipo", "status_margem", "status_publicacao")
    search_fields = ("sku_principal", "sku_secundario")
    ordering      = ["-criado_em"]
    readonly_fields = (
        "preco_sugerido", "margem_real", "estrategia_gerada",
        "status_margem", "processado_em", "criado_em", "atualizado_em",
        "painel_resultado",
    )

    fieldsets = (
        ("📋 Configuração do Anúncio", {
            "fields": ("tipo", "status_publicacao"),
        }),
        ("📦 Produto Principal", {
            "fields": (("sku_principal", "qtd_principal"),),
        }),
        ("🔀 Produto Secundário (somente MISTO)", {
            "fields": (("sku_secundario", "qtd_secundario"),),
            "classes": ("collapse",),
            "description": "Preencher apenas para anúncios do tipo MISTO.",
        }),
        ("🎯 Concorrente", {
            "fields": (("preco_concorrente", "link_concorrente"),),
            "description": "Obrigatório para análise verde/amarelo/vermelho.",
        }),
        ("🧮 Resultado do Cálculo", {
            "fields": ("painel_resultado", "estrategia_gerada", "processado_em"),
            "description": "Preenchido automaticamente pelo PricingEngine.",
        }),
        ("💬 Observações", {
            "fields": ("observacao",),
            "classes": ("collapse",),
        }),
        ("🕐 Auditoria", {
            "fields": ("criado_em", "atualizado_em"),
            "classes": ("collapse",),
        }),
    )

    # ── Colunas ───────────────────────────────────────────────────────────────

    @admin.display(description="Tipo")
    def badge_tipo(self, obj):
        cores = {
            "UNIT":  ("#1a73e8", "🎯 UNIT"),
            "MULTI": ("#7b2ff7", "📦 MULTI"),
            "MISTO": ("#e8710a", "🔀 MISTO"),
        }
        cor, label = cores.get(obj.tipo, ("gray", obj.tipo))
        return format_html(
            '<span style="color:{};font-weight:600;">{}</span>', cor, label
        )

    @admin.display(description="Composição")
    def descricao_kit_display(self, obj):
        return obj.descricao_kit

    @admin.display(description="Status Margem")
    def badge_status_margem(self, obj):
        cfg = {
            "pendente": ("⏳", "#888",    "#1e1e1e"),
            "verde":    ("🟢", "#00c853", "#0a1f10"),
            "amarelo":  ("🟡", "#f5c400", "#1f1800"),
            "vermelho": ("🔴", "#ff4d6d", "#1f0008"),
        }
        emoji, cor, bg = cfg.get(obj.status_margem, ("?", "gray", "transparent"))
        label = obj.get_status_margem_display()
        return format_html(
            '<span style="color:{};background:{};padding:2px 8px;border-radius:10px;'
            'font-size:12px;font-weight:600;">{} {}</span>',
            cor, bg, emoji, label.split("—")[0].strip(),
        )

    @admin.display(description="Publicação")
    def badge_status_publicacao(self, obj):
        cfg = {
            "fila":      ("📋", "#888"),
            "publicado": ("✅", "#00c853"),
            "bloqueado": ("🔴", "#ff4d6d"),
            "revisao":   ("🔄", "#f5c400"),
        }
        emoji, cor = cfg.get(obj.status_publicacao, ("?", "gray"))
        return format_html('<span style="color:{}">{} {}</span>', cor, emoji, obj.get_status_publicacao_display())

    @admin.display(description="Preço Sugerido")
    def preco_sugerido_display(self, obj):
        if obj.preco_sugerido is None:
            return format_html('<span style="color:#888">—</span>')
        return format_html('<b>R$ {:.2f}</b>', obj.preco_sugerido)

    @admin.display(description="Margem Real")
    def margem_real_display(self, obj):
        if obj.margem_real is None:
            return format_html('<span style="color:#888">—</span>')
        pct = float(obj.margem_real) * 100
        cor = "#00c853" if pct >= 12 else "#f5c400" if pct >= 5 else "#ff4d6d"
        return format_html('<span style="color:{};font-weight:600;">{:.1f}%</span>', cor, pct)

    @admin.display(description="Resultado")
    def painel_resultado(self, obj):
        if obj.status_margem == FilaAnuncio.StatusMargem.PENDENTE:
            return format_html(
                '<span style="color:#888;">⏳ Ainda não processado. '
                'Use a ação "Calcular preço agora" para processar.</span>'
            )
        cfg = {
            "verde":    ("#00c853", "🟢"),
            "amarelo":  ("#f5c400", "🟡"),
            "vermelho": ("#ff4d6d", "🔴"),
        }
        cor, emoji = cfg.get(obj.status_margem, ("#888", "?"))
        margem_str = f"{float(obj.margem_real)*100:.1f}%" if obj.margem_real else "—"
        return format_html(
            '<div style="background:#111;border-left:3px solid {};padding:10px 14px;border-radius:6px;">'
            '<b style="color:{};">{} R$ {:.2f}</b> &nbsp;|&nbsp; '
            'Margem: <b>{}</b>'
            '</div>',
            cor, cor, emoji,
            obj.preco_sugerido or 0,
            margem_str,
        )

    # ── Ações ─────────────────────────────────────────────────────────────────

    actions = [
        "calcular_preco_agora",
        "marcar_publicado",
        "marcar_bloqueado",
        "marcar_revisao",
        "resetar_para_pendente",
    ]

    @admin.action(description="⚡ Calcular preço agora (PricingEngine)")
    def calcular_preco_agora(self, request, queryset):
        processador = ProcessadorFila()
        ok = 0
        erros = []
        for item in queryset:
            try:
                processador.processar_item(item)
                ok += 1
            except Exception as e:
                erros.append(f"id={item.pk}: {e}")

        if ok:
            self.message_user(request, f"✅ {ok} item(s) processado(s).", messages.SUCCESS)
        if erros:
            self.message_user(request, f"⚠️ Erros: {'; '.join(erros)}", messages.WARNING)

    @admin.action(description="✅ Marcar como Publicado")
    def marcar_publicado(self, request, queryset):
        # Bloqueia publicação de itens vermelhos
        vermelhos = queryset.filter(status_margem=FilaAnuncio.StatusMargem.VERMELHO)
        if vermelhos.exists():
            self.message_user(
                request,
                f"🔴 {vermelhos.count()} item(s) com status VERMELHO não podem ser publicados. "
                "Remova-os da seleção ou avise o gestor.",
                messages.ERROR,
            )
            return
        total = queryset.update(status_publicacao=FilaAnuncio.StatusPublicacao.PUBLICADO)
        self.message_user(request, f"✅ {total} item(s) marcado(s) como Publicado.", messages.SUCCESS)

    @admin.action(description="🔴 Marcar como Bloqueado")
    def marcar_bloqueado(self, request, queryset):
        total = queryset.update(status_publicacao=FilaAnuncio.StatusPublicacao.BLOQUEADO)
        self.message_user(request, f"🔴 {total} item(s) bloqueado(s).", messages.WARNING)

    @admin.action(description="🔄 Enviar para Revisão")
    def marcar_revisao(self, request, queryset):
        total = queryset.update(status_publicacao=FilaAnuncio.StatusPublicacao.REVISAO)
        self.message_user(request, f"🔄 {total} item(s) enviado(s) para revisão.", messages.WARNING)

    @admin.action(description="⏳ Resetar para Pendente (recalcular)")
    def resetar_para_pendente(self, request, queryset):
        total = queryset.update(
            status_margem=FilaAnuncio.StatusMargem.PENDENTE,
            preco_sugerido=None,
            margem_real=None,
            estrategia_gerada=None,
            processado_em=None,
        )
        self.message_user(request, f"⏳ {total} item(s) resetado(s) para recalcular.", messages.SUCCESS)
