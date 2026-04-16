import sys
from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.db import connection

from .services.criador_kits_service import CriadorKitsService

def painel_kits(request):
    if 'ignorados' not in request.session:
        request.session['ignorados'] = []

    contexto = {"status": "AGUARDANDO", "dados": {}}

    # ESTATÍSTICAS
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT proximo_numero FROM public.controle_sequencia WHERE id = 1")
            row = cursor.fetchone()
            seq_base = row[0] if row else 0
            
            cursor.execute("SELECT COUNT(*) FROM public.numero_reciclado")
            qtd_reciclados = cursor.fetchone()[0]
            
            # Próximo Real = Primeiro da fila de reciclagem OU a sequência base
            proximo_real = seq_base
            if qtd_reciclados > 0:
                cursor.execute("SELECT numero FROM public.numero_reciclado ORDER BY numero ASC LIMIT 1")
                proximo_real = cursor.fetchone()[0]

            contexto["stats"] = {
                "proximo_real": proximo_real,
                "sequencia_base": seq_base,
                "reciclados": qtd_reciclados
            }
    except Exception:
        contexto["stats"] = {"proximo_real": "-", "sequencia_base": "-", "reciclados": 0}

    if request.method == "POST":
        acao = request.POST.get("acao")
        servico = CriadorKitsService()

        # 1. ANALISAR E CRIAR
        if acao == "analisar":
            resultado = servico.buscar_proxima_pendencia(request.session['ignorados'])
            contexto["status"] = resultado["status"]
            contexto["dados"] = resultado

            if resultado.get("status") == "NOVO":
                resp_criacao = servico.executar_criacao(resultado["pid"], resultado["sku_origem"])
                if resp_criacao["sucesso"]:
                    messages.success(request, f"✅ {resp_criacao['msg']}")
                    return redirect("painel_kits") 
                else:
                    messages.error(request, f"❌ {resp_criacao['msg']}")

        # 2. CALIBRAR (FORÇA BRUTA)
        elif acao == "calibrar":
            # Isso vai demorar um pouco pois vai varrer tudo
            resp = servico.calibrar_contador_com_baselinker()
            if resp["sucesso"]:
                messages.success(request, resp["msg"])
            else:
                messages.warning(request, resp["msg"])
            return redirect("painel_kits")

        # 3. MANUAL
        elif acao == "criar_manual":
            sku_manual = request.POST.get("sku_manual")
            if not sku_manual:
                messages.error(request, "Digite o SKU.")
                return redirect("painel_kits")

            pid = servico.buscar_pid_por_sku(sku_manual)
            if not pid:
                messages.error(request, f"SKU {sku_manual} não encontrado.")
                return redirect("painel_kits")

            resp = servico.executar_criacao(pid, sku_manual)
            if resp["sucesso"]:
                messages.success(request, f"✅ Manual criado: {resp['sku_novo']}")
            else:
                messages.error(request, f"Falha: {resp['msg']}")
            return redirect("painel_kits")

        # Ações Auxiliares
        elif acao == "criar_duplicado":
            pid = request.POST.get("pid")
            sku = request.POST.get("sku_origem")
            resp = servico.executar_criacao(pid, sku, sku_desejado=sku)
            if resp["sucesso"]:
                messages.success(request, f"Duplicata: {resp['sku_novo']}")
            else:
                messages.error(request, resp["msg"])
            return redirect("painel_kits")

        elif acao == "pular":
            pid = request.POST.get("pid")
            request.session['ignorados'].append(pid)
            messages.info(request, "Ignorado.")
            return redirect("painel_kits")

        elif acao == "limpar_sessao":
            request.session['ignorados'] = []
            messages.info(request, "Cache limpo.")
            return redirect("painel_kits")

    return render(request, "painel_kits.html", contexto)

@csrf_exempt
def verificar_status_robo(request): return JsonResponse({"status": "Inativo", "color": "gray"})
@csrf_exempt
def iniciar_robo(request): return JsonResponse({"status": "Inativo", "color": "gray"})
@csrf_exempt
def parar_robo(request): return JsonResponse({"status": "Inativo", "color": "gray"})