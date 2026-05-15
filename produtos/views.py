from django.shortcuts import render
from core.decorators import cadastro_required

# AQUI ESTÁ A CORREÇÃO (Linha 2):
from .services.gerador_ean import GeradorEANService


@cadastro_required
def gerar_ean(request):
    ...

def gerador_ean_view(request):
    contexto = {"resultado": None, "erro": None}

    if request.method == "POST":
        sku = request.POST.get("sku", "").strip()
        gpc = request.POST.get("gpc", "").strip()

        if not sku or not gpc:
            contexto["erro"] = "Preencha SKU e GPC."
        else:
            try:
                # AQUI ESTÁ A SEGUNDA CORREÇÃO (Linha 16 agora):
                service = GeradorEANService()
                resposta = service.processar_ean_avulso(sku, gpc)
                
                if resposta["sucesso"]: 
                    contexto["resultado"] = resposta
                else: 
                    contexto["erro"] = resposta["erro"]
            except Exception as e:
                contexto["erro"] = f"Falha Crítica: {str(e)}"

    return render(request, "produtos/gerar_ean.html", contexto)