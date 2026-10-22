"""
VIEW TEMPORÁRIA — Criação de grade de kits com título manual.

⚠️  ATENÇÃO: Este arquivo é TEMPORÁRIO e será removido após o período de uso.
    Para remover: delete este arquivo, o template painel_titulo_manual.html
    e a linha de URL correspondente em urls.py.

    Não modifica nenhum comportamento do fluxo principal (views.py / CriadorKitsService).
"""

import re
from typing import Optional

from django.shortcuts import render, redirect
from django.contrib import messages
from django.views.decorators.cache import never_cache

from core.decorators import cadastro_required
from integracoes.mercadolivre import MercadoLivreClient
from .services.criador_kits_service import (
    CriadorKitsService,
    ajustar_titulo,
)


# ---------------------------------------------------------------------------
# Utilitário local — derivar títulos de kit a partir do título unitário
# ---------------------------------------------------------------------------

_PALAVRAS_QUANTIDADE = re.compile(
    r'\b(kit\s*\d*|kits|unidade|unidades|unitário|unitaria|pack|original|clássico|classico|premium)\b',
    flags=re.IGNORECASE,
)

_PALAVRAS_FIM_PROIBIDAS = {
    "e", "para", "com", "de", "o", "a", "os", "as",
    "em", "do", "da", "dos", "das", "por", "no", "na",
}


def _limpar_base(titulo: str) -> str:
    """Remove termos de quantidade/modalidade do título antes de derivar os kits."""
    base = _PALAVRAS_QUANTIDADE.sub(' ', titulo)
    base = re.sub(r'\s{2,}', ' ', base).strip()
    return base


def _cortar_para_caber(base: str, max_chars: int) -> str:
    """Corta palavras do final até o título caber em max_chars. Remove órfãos no final."""
    titulo = base
    while len(titulo) > max_chars:
        if ' ' in titulo:
            titulo = titulo.rsplit(' ', 1)[0].strip()
        else:
            titulo = titulo[:max_chars]
            break

    # Remove preposições/conjunções soltas no final
    while True:
        palavras = titulo.split()
        if not palavras:
            break
        if palavras[-1].lower() in _PALAVRAS_FIM_PROIBIDAS:
            palavras.pop()
            titulo = ' '.join(palavras)
        else:
            break

    return titulo.strip()


def derivar_titulos_kit(
    titulo_unitario: str,
    titulo_kit2: str = "",
    titulo_kit3: str = "",
    titulo_kit4: str = "",
) -> dict:
    """
    Monta os 8 títulos da grade a partir do título unitário.

    - título_unitario  → usado em 1_classico e 1_premium (exatamente como fornecido)
    - titulo_kit2/3/4  → se fornecidos, usados diretamente (ex: "Kit 48 Feltro...")
                         se em branco, derivados automaticamente com prefixo "Kit N "

    Retorna:
        {
            "1_classico": str,
            "1_premium":  str,
            "2_classico": str,
            "2_premium":  str,
            "3_classico": str,
            "3_premium":  str,
            "4_classico": str,
            "4_premium":  str,
        }
    """
    titulo_unit = ajustar_titulo(titulo_unitario, min_c=0, max_c=60)
    base = _limpar_base(titulo_unit)

    resultado = {
        "1_classico": titulo_unit,
        "1_premium": titulo_unit,
    }

    titulos_manuais = {
        2: titulo_kit2.strip(),
        3: titulo_kit3.strip(),
        4: titulo_kit4.strip(),
    }

    for qtd in [2, 3, 4]:
        manual = titulos_manuais[qtd]
        if manual:
            # Título informado diretamente — valida e usa sem alterar
            titulo_final = ajustar_titulo(manual, min_c=0, max_c=60)
        else:
            # Derivação automática a partir do título base
            prefixo = f"Kit {qtd} "
            chars_disponiveis = 60 - len(prefixo)
            titulo_kit = _cortar_para_caber(base, chars_disponiveis)
            titulo_final = f"{prefixo}{titulo_kit}"

        resultado[f"{qtd}_classico"] = titulo_final
        resultado[f"{qtd}_premium"] = titulo_final

    return resultado



def _resolver_category_ml(categoria_input: str) -> Optional[str]:
    if not categoria_input:
        return None
    try:
        return MercadoLivreClient().resolver_categoria_por_input(categoria_input)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# View principal
# ---------------------------------------------------------------------------

@cadastro_required
@never_cache
def painel_titulo_manual(request):
    """
    Painel temporário: cria grade de kits com títulos informados manualmente.

    Suporta dois modos:
    - Simples: só o título unitário → Kit 2/3/4 derivados automaticamente
    - Completo: 4 títulos independentes (ex: Kit 24 / Kit 48 / Kit 72 / Kit 96)
    """
    servico = CriadorKitsService()
    preview_titulos = None
    form_data = {}

    if request.method == 'POST':
        acao           = request.POST.get('acao', '')
        sku_manual     = request.POST.get('sku_manual', '').strip()
        titulo_manual  = request.POST.get('titulo_manual', '').strip()
        titulo_kit2    = request.POST.get('titulo_kit2', '').strip()
        titulo_kit3    = request.POST.get('titulo_kit3', '').strip()
        titulo_kit4    = request.POST.get('titulo_kit4', '').strip()
        categoria_input = request.POST.get('categoria_ml', '').strip() or None
        gerar_ean_unitario = request.POST.get('gerar_ean_unitario') == 'on'
        forcar_novos_gtins = request.POST.get('forcar_novos_gtins') == 'on' or gerar_ean_unitario
        gpc_manual     = request.POST.get('gpc_manual', '').strip() or None

        form_data = {
            'sku_manual':        sku_manual,
            'titulo_manual':     titulo_manual,
            'titulo_kit2':       titulo_kit2,
            'titulo_kit3':       titulo_kit3,
            'titulo_kit4':       titulo_kit4,
            'categoria_ml':      categoria_input or '',
            'gpc_manual':        gpc_manual or '',
            'gerar_ean_unitario': gerar_ean_unitario,
            'forcar_novos_gtins': forcar_novos_gtins,
        }

        # Valida títulos com comprimento > 60
        def _titulo_muito_longo(nome, valor):
            if valor and len(valor) > 60:
                messages.error(request, f'Título "{nome}" tem {len(valor)} chars — máximo é 60.')
                return True
            return False

        # ── Ação: pré-visualizar ─────────────────────────────────────────────
        if acao == 'preview':
            if not titulo_manual:
                messages.error(request, 'Informe ao menos o título unitário para pré-visualizar.')
            elif any([
                _titulo_muito_longo('Unitário', titulo_manual),
                _titulo_muito_longo('Variação 2', titulo_kit2),
                _titulo_muito_longo('Variação 3', titulo_kit3),
                _titulo_muito_longo('Variação 4', titulo_kit4),
            ]):
                pass  # erro já adicionado dentro de _titulo_muito_longo
            else:
                preview_titulos = derivar_titulos_kit(
                    titulo_manual,
                    titulo_kit2=titulo_kit2,
                    titulo_kit3=titulo_kit3,
                    titulo_kit4=titulo_kit4,
                )

            return render(request, 'painel_titulo_manual.html', {
                'preview': preview_titulos,
                'form': form_data,
            })

        # ── Ação: criar grade ────────────────────────────────────────────────
        if acao == 'confirmar_criacao':

            if not sku_manual:
                messages.error(request, 'Informe o SKU do produto principal.')
                return render(request, 'painel_titulo_manual.html', {'form': form_data})

            if not titulo_manual:
                messages.error(request, 'Informe o título unitário.')
                return render(request, 'painel_titulo_manual.html', {'form': form_data})

            erros = any([
                _titulo_muito_longo('Unitário', titulo_manual),
                _titulo_muito_longo('Variação 2', titulo_kit2),
                _titulo_muito_longo('Variação 3', titulo_kit3),
                _titulo_muito_longo('Variação 4', titulo_kit4),
            ])
            if erros:
                return render(request, 'painel_titulo_manual.html', {'form': form_data})

            if not categoria_input:
                messages.error(request, 'Informe a árvore de categoria do Mercado Livre.')
                return render(request, 'painel_titulo_manual.html', {'form': form_data})

            pid = servico.buscar_pid_por_sku(sku_manual)
            if not pid:
                messages.error(request, f'SKU {sku_manual} não encontrado no BaseLinker.')
                return render(request, 'painel_titulo_manual.html', {'form': form_data})

            category_id_ml = _resolver_category_ml(categoria_input)
            if not category_id_ml:
                messages.error(request, f'Categoria não localizada no ML a partir de "{categoria_input}".')
                return render(request, 'painel_titulo_manual.html', {'form': form_data})

            titulos_forcados = derivar_titulos_kit(
                titulo_manual,
                titulo_kit2=titulo_kit2,
                titulo_kit3=titulo_kit3,
                titulo_kit4=titulo_kit4,
            )

            resp = servico.executar_criacao_grade(
                pid=pid,
                sku_origem=sku_manual,
                category_id_ml=category_id_ml,
                gerar_ean_unitario=gerar_ean_unitario,
                gpc_manual=gpc_manual,
                titulos_forcados=titulos_forcados,
                forcar_novos_gtins=forcar_novos_gtins,
            )

            if resp.get('sucesso'):
                messages.success(
                    request,
                    f"✅ Grade criada com {len(resp['skus'])} anúncios! SKUs: {', '.join(resp['skus'][:4])}..."
                )
                for alerta in resp.get('alertas_dimensao', []):
                    messages.warning(request, f'⚠️ {alerta}')
                return redirect('painel_titulo_manual')
            else:
                messages.error(request, f"Falha: {resp.get('msg', 'Erro desconhecido.')}")

    return render(request, 'painel_titulo_manual.html', {'form': form_data})

