from typing import Optional
from django.shortcuts import render, redirect
from django.contrib import messages
from django.utils import timezone
from datetime import timedelta
from core.decorators import cadastro_required
from django.views.decorators.cache import never_cache

from integracoes.mercadolivre import MercadoLivreClient, CATEGORIA_PADRAO_BASELINKER
from .services.criador_kits_service import CriadorKitsService
from .models.historico_kit import HistoricoKit

MAX_SKUS_SECUNDARIOS_MISTO = 20


def _normalizar_skus_secundarios(skus_raw, sku_principal: str, max_skus: int = MAX_SKUS_SECUNDARIOS_MISTO):
    """Normaliza, elimina duplicados e aplica o limite de 20 SKUs secundários para kit misto."""
    skus_secundarios = []
    for sku in skus_raw or []:
        s_clean = str(sku).strip()
        if not s_clean or s_clean == sku_principal:
            continue
        if s_clean not in skus_secundarios:
            skus_secundarios.append(s_clean)
        if len(skus_secundarios) >= max_skus:
            break
    return skus_secundarios


def _resolver_category_ml(categoria_input: str) -> Optional[str]:
    """
    Dado o input manual da categoria (árvore de categorias, URL ou ID direto),
    retorna o category_id resolvido no Mercado Livre.
    """
    if not categoria_input:
        return None
    try:
        return MercadoLivreClient().resolver_categoria_por_input(categoria_input)
    except Exception:
        return None


@cadastro_required
@never_cache
def painel_kits(request):
    """Renderiza e gerencia o painel de criação direta de grade de kits."""
    servico      = CriadorKitsService()
    erro_detalhe = None

    if request.method == "POST":
        acao               = request.POST.get("acao")
        sku_manual         = request.POST.get("sku_manual", "").strip()
        skus_raw           = request.POST.getlist("sku_secundario")
        categoria_input    = request.POST.get("categoria_ml", "").strip() or request.POST.get("url_produto_ml", "").strip() or None
        gerar_ean_unitario = request.POST.get("gerar_ean_unitario") == "on"

        # Resolve, unique, and filter out empty and primary SKU, respecting the 20-SKU mix cap.
        skus_secundarios = _normalizar_skus_secundarios(skus_raw, sku_manual, max_skus=20)
        e_misto = len(skus_secundarios) > 0

        if acao == "confirmar_criacao":
            gpc_manual = request.POST.get("gpc_manual", "").strip() or None
            
            if not sku_manual:
                messages.error(request, "Digite o SKU do produto principal.")
                return redirect("painel_kits")

            if not categoria_input:
                messages.error(request, "Informe a árvore de categoria do Mercado Livre (ex: Casa, Móveis e Decoração / Banheiros / Sifões ou MLB277472).")
                return redirect("painel_kits")

            pid = servico.buscar_pid_por_sku(sku_manual)
            if not pid:
                messages.error(request, f"SKU {sku_manual} não encontrado no BaseLinker.")
                return redirect("painel_kits")

            # Resolve a categoria manualmente via árvore, URL ou ID direto (sem adivinhação automática por título)
            category_id_ml = _resolver_category_ml(categoria_input)
            if not category_id_ml:
                messages.error(request, f"Não foi possível localizar uma categoria válida no Mercado Livre a partir de '{categoria_input}'. Verifique a árvore informada.")
                return redirect("painel_kits")

            # Criação da Grade Completa (6 Anúncios no BaseLinker)
            resp = servico.executar_criacao_grade(
                pid=pid,
                sku_origem=sku_manual,
                category_id_ml=category_id_ml,
                gerar_ean_unitario=gerar_ean_unitario,
                gpc_manual=gpc_manual,
                skus_secundarios=skus_secundarios,
            )
            if resp["sucesso"]:
                tipo = "Kit Misto" if e_misto else "Kit"
                messages.success(request, f"✅ Grade de {tipo} ({len(resp['skus'])} anúncios) criada com sucesso! SKUs: {', '.join(resp['skus'][:3])}...")
                # Exibe alertas de dimensão suspeita, se houver
                for alerta in resp.get("alertas_dimensao", []):
                    messages.warning(request, f"⚠️ Dimensão suspeita detectada: {alerta}")
                return redirect("painel_kits")
            else:
                messages.error(request, f"Falha na criação da grade: {resp['msg']}")
                erro_detalhe = resp.get("erro_detalhe")

        elif acao == "rollback":
            sku_rollback = request.POST.get("sku_rollback", "").strip()
            if not sku_rollback:
                messages.error(request, "Digite o SKU de origem para realizar o desfazer (rollback).")
                return redirect("painel_kits")

            resp = servico.executar_rollback_grade(sku_rollback)
            if resp["sucesso"]:
                messages.success(request, f"✅ {resp['msg']}")
            else:
                messages.error(request, f"❌ {resp['msg']}")
            return redirect("painel_kits")

        elif acao == "excluir_individual":
            id_kit = request.POST.get("id_kit", "").strip()
            sku_kit = request.POST.get("sku_kit", "").strip()
            if not id_kit or not sku_kit:
                messages.error(request, "Dados ausentes para a exclusão individual.")
                return redirect("painel_kits")

            resp = servico.executar_exclusao_individual(id_kit, sku_kit)
            if resp["sucesso"]:
                messages.success(request, f"✅ {resp['msg']}")
            else:
                messages.error(request, f"❌ {resp['msg']}")
            return redirect("painel_kits")

        elif acao == "cancelar_lote":
            sku_origem = request.POST.get("sku_origem", "").strip()
            if sku_origem:
                from django.core.cache import cache
                cache.set(f"criacao_lote_{sku_origem}", "cancelado", timeout=120)
                from django.http import JsonResponse
                return JsonResponse({"sucesso": True, "msg": f"Interrupção solicitada com sucesso para o SKU {sku_origem}."})
            from django.http import JsonResponse
            return JsonResponse({"sucesso": False, "msg": "SKU de origem ausente."}, status=400)

    agora       = timezone.now()
    inicio_hoje = agora.replace(hour=0, minute=0, second=0, microsecond=0)

    return render(request, "painel_kits.html", {
        "total_kits":   HistoricoKit.objects.count(),
        "kits_hoje":    HistoricoKit.objects.filter(criado_em__gte=inicio_hoje).count(),
        "kits_semana":  HistoricoKit.objects.filter(criado_em__gte=agora - timedelta(days=7)).count(),
        "ultimos_kits": HistoricoKit.objects.order_by("-criado_em")[:10],
        "erro_detalhe": erro_detalhe,
    })