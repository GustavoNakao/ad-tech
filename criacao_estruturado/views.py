from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from datetime import timedelta
from core.decorators import cadastro_required

from integracoes.mercadolivre import MercadoLivreClient, CATEGORIA_PADRAO_BASELINKER
from .services.criador_kits_service import CriadorKitsService
from .models.historico_kit import HistoricoKit


def _resolver_category_ml(url_produto_ml: str) -> str:
    """
    Dado o link de um produto no ML, retorna o category_id via GET /items/MLBXXXXXX.
    Se a URL estiver vazia, inválida ou a chamada falhar → retorna o fallback (979589).
    """
    if not url_produto_ml:
        return CATEGORIA_PADRAO_BASELINKER
    try:
        return MercadoLivreClient().buscar_category_id_por_url(url_produto_ml)
    except Exception:
        return CATEGORIA_PADRAO_BASELINKER


def _analisar_com_grade(servico: CriadorKitsService, sku: str, sku_secundario: str = None, category_id_ml: str = None) -> dict | None:
    """
    Monta a análise completa com grade de preços (promoção + vitrine) para o painel.
    Suporta Kit Misto com SKU secundário.
    """
    try:
        from precificacao.services.calculadora import Calculadora
        Calculadora.limpar_cache()

        pid = servico.buscar_pid_por_sku(sku)
        if not pid:
            return None
        dados = servico._baixar_dados_produto(pid)
        if not dados:
            return None

        e_misto = bool(sku_secundario)

        # Kit Misto — combina dados dos dois produtos
        if e_misto:
            pid2   = servico.buscar_pid_por_sku(sku_secundario)
            dados2 = servico._baixar_dados_produto(pid2) if pid2 else None
            if dados2:
                custo1 = servico._extrair_custo(dados)
                custo2 = servico._extrair_custo(dados2)
                peso1  = float(dados.get("weight") or 0)
                peso2  = float(dados2.get("weight") or 0)
                dados_calc = {
                    "average_cost": custo1 + custo2,
                    "weight":       peso1 + peso2,
                    "tax_rate":     dados.get("tax_rate") or dados2.get("tax_rate") or 27.25,
                    "height":       dados.get("height", 0),
                    "width":        dados.get("width", 0),
                    "length":       dados.get("length", 0),
                }
            else:
                dados_calc = dados
        else:
            dados_calc = dados

        grade = Calculadora.calcular_grade(dados_calc, sku_ref=sku, category_id=category_id_ml)

        if grade.bloqueado:
            return {
                "bloqueado":       True,
                "motivo_bloqueio": grade.motivo_bloqueio,
                "e_misto":         e_misto,
            }

        from precificacao.models.regras import RegrasPrecificacao
        regras = RegrasPrecificacao.get_ativa()
        piso   = float(regras.piso_frete_gratis)

        # Monta dados dos 8 anúncios para o template
        anuncios_dict = []
        for a in grade.anuncios:
            anuncios_dict.append({
                "referencia":      a.referencia,
                "quantidade":      a.quantidade,
                "modalidade":      a.modalidade,
                "preco_promocao":    a.preco_promocao,
                "preco_vitrine":   a.preco_vitrine,
                "custo_logistico": a.custo_logistico,
                "tarifa_reduzida": a.tarifa_reduzida_aplicada,
                "bloqueado":       a.bloqueado,
                "erros":           a.erros,
                "acima_piso":      a.preco_promocao >= piso,
            })

        unit = next((a for a in grade.anuncios if a.referencia == "unit_1_classico"), None)
        p1   = unit.preco_promocao if unit and not unit.bloqueado else piso

        p2 = next((a.preco_promocao for a in grade.anuncios if a.referencia == "unit_2_classico" and not a.bloqueado), 0)
        p3 = next((a.preco_promocao for a in grade.anuncios if a.referencia == "unit_3_classico" and not a.bloqueado), 0)

        # piso_aplicado: ResultadoAnuncio não carrega esse flag diretamente.
        # Derivamos comparando preco_promocao com piso_valor_forcado das regras ativas.
        piso_valor_forcado = float(regras.piso_valor_forcado)
        piso_aplicado_unit = (unit.preco_promocao == piso_valor_forcado) if unit and not unit.bloqueado else True

        if unit and not piso_aplicado_unit and p1 >= piso:
            status, emoji, label = "verde",    "🟢", "STATUS VERDE — PODE CADASTRAR"
            mensagem = "Pode seguir com o preço sugerido. Estamos competitivos e com lucro."
        elif not unit or unit.bloqueado:
            status, emoji, label = "vermelho", "🔴", "STATUS VERMELHO — NÃO CADASTRAR"
            mensagem = "NÃO CADASTRAR. Verifique o custo e o peso do produto."
        else:
            status, emoji, label = "amarelo",  "🟡", "STATUS AMARELO — ATENÇÃO"
            mensagem = "Não tente baixar o preço. Foque em Benefícios e Qualidade R831."

        alerta_upsell = None
        if p1 > 0 and p2 > 0:
            delta12 = p2 - p1
            if 0 < delta12 < 10.00:
                alerta_upsell = f"⚡ UPSELL: Kit 2 (R${p2:.2f}) está R${delta12:.2f} acima do Unitário (R${p1:.2f})."

        return {
            "bloqueado":       False,
            "e_misto":         e_misto,
            "anuncios":        anuncios_dict,
            "preco_unitario":  round(p1, 2),
            "preco_kit_2":     round(p2, 2),
            "preco_kit_3":     round(p3, 2),
            "preco_unit_kit2": round(p2 / 2, 2) if p2 else 0,
            "preco_unit_kit3": round(p3 / 3, 2) if p3 else 0,
            "alerta_upsell":   alerta_upsell,
            "status":          status,
            "status_emoji":    emoji,
            "status_label":    label,
            "status_mensagem": mensagem,
        }

    except Exception as exc:
        import logging
        logging.getLogger("gerador_kits").warning(f"Análise falhou para SKU={sku}: {exc}")
        return None


@cadastro_required
def painel_kits(request):
    servico      = CriadorKitsService()
    analise      = None
    erro_detalhe = None

    if request.method == "POST":
        acao           = request.POST.get("acao")
        sku_manual     = request.POST.get("sku_manual", "").strip()
        sku_secundario = request.POST.get("sku_secundario", "").strip() or None
        url_produto_ml = request.POST.get("url_produto_ml", "").strip() or None
        e_misto        = bool(sku_secundario)

        if acao == "analisar":
            if not sku_manual:
                messages.error(request, "Digite o SKU do produto.")
                return redirect("painel_kits")

            pid = servico.buscar_pid_por_sku(sku_manual)
            if not pid:
                messages.error(request, f"SKU {sku_manual} não encontrado no BaseLinker.")
                return redirect("painel_kits")

            # Resolve category_id do ML via URL do produto
            category_id_ml = _resolver_category_ml(url_produto_ml)

            # Análise de precificação
            analise = _analisar_com_grade(servico, sku_manual, sku_secundario, category_id_ml)

            if analise:
                analise["sku_manual"] = sku_manual
                analise["sku_secundario"] = sku_secundario or ""
                analise["category_id_ml"] = category_id_ml
                analise["url_produto_ml"] = url_produto_ml or ""
                analise["categoria_origem"] = (
                    "via URL do produto" if url_produto_ml else "padrão Estruturado"
                )
            else:
                messages.error(request, "Falha ao analisar o produto. Verifique os dados.")

        elif acao == "confirmar_criacao":
            category_id_ml = request.POST.get("category_id_ml")
            
            if not sku_manual:
                messages.error(request, "Dados de SKU ausentes para criação.")
                return redirect("painel_kits")

            pid = servico.buscar_pid_por_sku(sku_manual)
            if not pid:
                messages.error(request, "Produto não encontrado para criação.")
                return redirect("painel_kits")

            # Criação da Grade Completa (8 Anúncios no BaseLinker)
            resp = servico.executar_criacao_grade(
                pid=pid,
                sku_origem=sku_manual,
                sku_secundario=sku_secundario,
                category_id_ml=category_id_ml,
            )
            if resp["sucesso"]:
                tipo = "Kit Misto" if e_misto else "Kit"
                messages.success(request, f"✅ Grade de {tipo} (8 anúncios) criada com sucesso! SKUs: {', '.join(resp['skus'][:3])}...")
                return redirect("painel_kits")
            else:
                messages.error(request, f"Falha na criação da grade: {resp['msg']}")
                erro_detalhe = resp.get("erro_detalhe")

    agora       = timezone.now()
    inicio_hoje = agora.replace(hour=0, minute=0, second=0, microsecond=0)

    return render(request, "painel_kits.html", {
        "total_kits":   HistoricoKit.objects.count(),
        "kits_hoje":    HistoricoKit.objects.filter(criado_em__gte=inicio_hoje).count(),
        "kits_semana":  HistoricoKit.objects.filter(criado_em__gte=agora - timedelta(days=7)).count(),
        "ultimos_kits": HistoricoKit.objects.order_by("-criado_em")[:10],
        "analise":      analise,
        "erro_detalhe": erro_detalhe,
    })


@csrf_exempt
def verificar_status_robo(request): return JsonResponse({"status": "Inativo"})
@csrf_exempt
def iniciar_robo(request): return JsonResponse({"status": "Inativo"})
@csrf_exempt
def parar_robo(request): return JsonResponse({"status": "Inativo"})