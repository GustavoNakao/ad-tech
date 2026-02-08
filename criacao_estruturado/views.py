import subprocess
import sys
from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.db import transaction

# Seus models
from .models.controle_sequencia import ControleSequencia
# Seu service novo
from .services.criador_kits_service import CriadorKitsService

# --- CONTROLE GLOBAIS DO ROBÔ ---
robo_processo = None
status_robo = "Parado"


def painel_kits(request):
    if 'ignorados' not in request.session:
        request.session['ignorados'] = []

    contexto = {"status": "AGUARDANDO", "dados": {}}

    if request.method == "POST":
        acao = request.POST.get("acao")
        servico = CriadorKitsService()

        # --- AÇÃO 1: ANALISAR E CRIAR UM POR VEZ ---
        if acao == "analisar":
            resultado = servico.buscar_proxima_pendencia(request.session['ignorados'])
            contexto["status"] = resultado["status"]
            contexto["dados"] = resultado

            if resultado.get("status") == "NOVO":
                # Tenta criar imediatamente
                resp_criacao = servico.executar_criacao(resultado["pid"], resultado["sku_origem"])
                if resp_criacao["sucesso"]:
                    messages.success(request, f"✅ {resp_criacao['msg']}")
                    contexto["status"] = "SUCESSO_AUTO"
                else:
                    messages.error(request, f"❌ {resp_criacao['msg']}")

        # --- AÇÃO 2: CALIBRAR CONTADOR COM O BASELINKER ---
        elif acao == "calibrar":
            resp = servico.calibrar_contador_com_baselinker()
            if resp["sucesso"]:
                messages.success(request, resp["msg"])
            else:
                messages.warning(request, resp["msg"])
            return redirect("painel_kits")

        # --- AÇÃO 3: DEFINIR INÍCIO MANUALMENTE ---
        elif acao == "definir_inicio":
            novo_num = request.POST.get("numero_inicial")
            if novo_num and novo_num.isdigit():
                try:
                    with transaction.atomic():
                        # select_for_update garante que nao estamos editando enquanto o robô tenta ler
                        seq, _ = ControleSequencia.objects.select_for_update().get_or_create(nome="Sequencia Kits")
                        seq.proximo_numero = int(novo_num)
                        seq.save()
                    messages.success(request, f"Sequência definida manualmente para {novo_num}")
                except Exception as e:
                    messages.error(request, f"Erro ao salvar sequência: {e}")
            return redirect("painel_kits")

        # --- AÇÃO 4: AUTOMAÇÃO TOTAL (LOOP) ---
        elif acao == "automacao_total":
            resultado = servico.processar_tudo_automaticamente()
            messages.success(request, resultado["msg"])
            return redirect("painel_kits")

        # --- AÇÃO 5: CRIAR DUPLICADO (FORÇADO) ---
        elif acao == "criar_duplicado":
            pid = request.POST.get("pid")
            sku = request.POST.get("sku_origem")
            # Passa o SKU de origem como `sku_desejado` para forçar uso desse número
            resp = servico.executar_criacao(pid, sku, sku_desejado=sku)
            if resp["sucesso"]:
                messages.success(request, f"🔥 Duplicata criada: {resp['sku_novo']}")
            else:
                messages.error(request, resp["msg"])
            return redirect("painel_kits")

        # --- AÇÃO 6: CRIAR MANUAL (PID + SKU fornecidos pelo usuário) ---
        elif acao == "criar_manual":
            sku_manual = request.POST.get("sku_manual")
            if not sku_manual:
                messages.error(request, "Informe o SKU para criação manual.")
                return redirect("painel_kits")

            # Procura o PID no BaseLinker a partir do SKU informado
            pid = servico.buscar_pid_por_sku(sku_manual)
            if not pid:
                messages.error(request, f"SKU {sku_manual} não encontrado no BaseLinker.")
                return redirect("painel_kits")

            # Não forçamos o SKU do kit para o mesmo do produto; usamos a geração automática
            resp = servico.executar_criacao(pid, sku_manual)
            if resp["sucesso"]:
                messages.success(request, f"✅ Criado manualmente: {resp['sku_novo']}")
            else:
                messages.error(request, f"Falha: {resp['msg']}")
            return redirect("painel_kits")

        # --- AÇÕES DE SESSÃO ---
        elif acao == "pular":
            pid = request.POST.get("pid")
            lista = request.session['ignorados']
            lista.append(pid)
            request.session['ignorados'] = lista
            messages.info(request, "Item ignorado temporariamente.")
            return redirect("painel_kits")

        elif acao == "limpar_sessao":
            request.session['ignorados'] = []
            messages.info(request, "Lista de ignorados limpa.")
            return redirect("painel_kits")

    return render(request, "painel_kits.html", contexto)


# -------------------------------------------------------------------------
# CONTROLE DE PROCESSO EM SEGUNDO PLANO (ROBÔ)
# -------------------------------------------------------------------------
def verificar_status_robo(request):
    global robo_processo, status_robo
    if robo_processo is not None:
        if robo_processo.poll() is None:
            status_robo = "Rodando..."
        else:
            status_robo = "Parado (Finalizado)"
            robo_processo = None

    cor = "#2ecc71" if "Rodando" in status_robo else "#95a5a6"
    if "Erro" in status_robo: cor = "#e74c3c"

    return JsonResponse({"status": status_robo, "color": cor})


@csrf_exempt
def iniciar_robo(request):
    global robo_processo, status_robo
    if request.method == "POST":
        if robo_processo is not None and robo_processo.poll() is None:
            return JsonResponse({"status": "Já está rodando!", "color": "orange"})
        try:
            # Chama o comando de gestão (management command)
            cmd = [sys.executable, "manage.py", "gerar_kits"]
            robo_processo = subprocess.Popen(cmd, shell=False)
            status_robo = "Rodando..."
            return JsonResponse({"status": "Iniciado!", "color": "#2ecc71"})
        except Exception as e:
            status_robo = f"Erro: {str(e)}"
            return JsonResponse({"status": f"Erro: {e}", "color": "red"})
    return JsonResponse({"status": "Erro"}, status=405)


@csrf_exempt
def parar_robo(request):
    global robo_processo, status_robo
    if request.method == "POST":
        if robo_processo and robo_processo.poll() is None:
            try:
                robo_processo.terminate()
                status_robo = "Parado Manualmente"
                return JsonResponse({"status": "Parado!", "color": "#e74c3c"})
            except Exception as e:
                return JsonResponse({"status": f"Erro: {e}", "color": "orange"})
        return JsonResponse({"status": "Já estava parado.", "color": "#95a5a6"})
    return JsonResponse({"status": "Erro"}, status=405)