from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .services.criador_kits_service import CriadorKitsService


def painel_kits(request):
    contexto = {}

    if request.method == "POST":
        acao = request.POST.get("acao")
        servico = CriadorKitsService()

        if acao == "criar_manual":
            sku_manual = request.POST.get("sku_manual", "").strip()
            if not sku_manual:
                messages.error(request, "Digite o SKU do produto.")
                return redirect("painel_kits")

            pid = servico.buscar_pid_por_sku(sku_manual)
            if not pid:
                messages.error(request, f"SKU {sku_manual} não encontrado no Baselinker.")
                return redirect("painel_kits")

            resp = servico.executar_criacao(pid, sku_manual)
            if resp["sucesso"]:
                messages.success(request, f"✅ Kit criado! SKU do kit: {resp['sku_novo']}")
            else:
                messages.error(request, f"❌ Falha: {resp['msg']}")
            return redirect("painel_kits")

    return render(request, "painel_kits.html", contexto)


# Endpoints legados mantidos para não quebrar URLs já configuradas
@csrf_exempt
def verificar_status_robo(request): return JsonResponse({"status": "Inativo"})
@csrf_exempt
def iniciar_robo(request): return JsonResponse({"status": "Inativo"})
@csrf_exempt
def parar_robo(request): return JsonResponse({"status": "Inativo"})
