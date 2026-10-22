from __future__ import annotations

import logging
import time
import uuid
import re
import json
from typing import Dict, List, Optional
from decimal import Decimal

from django.db import connection
from django.db.models import Q
from django.conf import settings
from decouple import config

from integracoes.baselinker.client import BaselinkerClient
from integracoes.mercadolivre.client import MercadoLivreClient
from integracoes.gs1.client import get_gs1_client
from integracoes.gemini import GeminiClient
from integracoes.gemini.client import baixar_imagem_para_base64
from integracoes.groq import GroqClient

from ..models.historico_kit import HistoricoKit
from ..models.gtin_kit import GtinKit
from ..models.numero_reciclado import NumeroReciclado
from precificacao.services.calculadora import Calculadora
from precificacao.models import RegrasPrecificacao
from categorias.models.termos_proibidos import TermosProibidos

from .ficha_tecnica.validator import is_unidades_por_kit_attribute

logger = logging.getLogger("gerador_kits")

PRICE_IDS: List[str] = [
    "53516", "24908", "25461", "25462", "25463", "25464", "25465",
    "25466", "25467", "25468", "25469", "28598", "29479", "29723",
    "31771", "31772", "40117", "40403", "40579", "40751", "40998",
    "41205", "41207", "41219", "48026",
]


# =============================================================================
# UTILITÁRIOS DE PÓS-PROCESSAMENTO (sem dependência de IA)
# =============================================================================

# Seções obrigatórias da descrição — usadas para validar a resposta da IA
SECOES_OBRIGATORIAS = [
    "SOBRE O PRODUTO",
    "BENEFÍCIOS",
    "DIFERENCIAIS DO PRODUTO",
    "DÚVIDAS FREQUENTES",
    "IDEAL PARA",
]


def ajustar_titulo(titulo: str, min_c: int = 55, max_c: int = 60) -> str:
    """
    Garante que o ttulo tenha no máximo max_c caracteres.
    - Se maior: remove a última palavra inteira até caber.
    NUNCA tenta inflar ou preencher com palavras de SEO padding.
    Também limpa preposições/conjunções órfãs no final do ttulo.
    """
    titulo = " ".join(titulo.split()).strip()  # normaliza espaços

    # 1. Corta excesso se maior que max_c
    while len(titulo) > max_c:
        if " " in titulo:
            titulo = titulo.rsplit(" ", 1)[0].strip()
        else:
            titulo = titulo[:max_c]
            break

    # 2. Remove preposições e conjunções soltas no final do ttulo
    palavras_fim_proibidas = {"e", "para", "com", "de", "o", "a", "os", "as", "em", "do", "da", "dos", "das"}
    while True:
        palavras = titulo.split()
        if not palavras:
            break
        ultima_palavra = palavras[-1].lower()
        if ultima_palavra in palavras_fim_proibidas:
            palavras.pop()
            titulo = " ".join(palavras)
        else:
            break
    return titulo.strip()



def formatar_title_case(titulo: str) -> str:
    """Primeira letra de cada palavra maiúscula, resto minúsculo. Preserva siglas."""
    SIGLAS = {"LED", "USB", "ML", "UV", "pH", "UHF", "VHF", "HDMI", "FM", "AM"}
    palavras = titulo.split()
    resultado = []
    for p in palavras:
        if p.upper() in SIGLAS:
            resultado.append(p.upper())
        else:
            resultado.append(p[0].upper() + p[1:].lower() if p else "")
    return " ".join(resultado)


def normalizar_texto_validador(texto: str) -> str:
    """
    Normaliza o texto para comparações robustas e flexíveis de cabeçalhos de seção:
    - Transforma em caixa baixa
    - Remove marcas de markdown (*, #, :, -, _, etc.)
    - Remove acentos/diacríticos
    - Normaliza múltiplos espaços
    """
    import unicodedata
    import re
    texto = (texto or "").lower()
    # Remove marcas de markdown e pontuações de título
    texto = re.sub(r'[*#:\-_]', ' ', texto)
    # Remove acentos
    texto_norm = unicodedata.normalize('NFD', texto)
    texto_sem_acentos = "".join(c for c in texto_norm if unicodedata.category(c) != 'Mn')
    return " ".join(texto_sem_acentos.split())


def _limpar_travessoes(texto: str) -> str:
    """Remove travessões (—, –, --) substituindo por dois-pontos ou hífens para leitura limpa."""
    if not texto:
        return ""
    res = texto.replace(" — ", ": ").replace("—", ": ")
    res = res.replace(" – ", ": ").replace("–", ": ")
    res = res.replace(" -- ", ": ")
    return res


SECOES_OBRIGATORIAS = [
    "SOBRE O PRODUTO",
    "PRINCIPAIS CARACTERÍSTICAS",
    "BENEFÍCIOS",
    "DÚVIDAS FREQUENTES",
    "IDEAL PARA",
]

SECOES_OBRIGATORIAS_NORM = {
    "SOBRE O PRODUTO": "sobre o produto",
    "PRINCIPAIS CARACTERÍSTICAS": "principais caracteristicas",
    "BENEFÍCIOS": "beneficios",
    "DÚVIDAS FREQUENTES": "duvidas frequentes",
    "IDEAL PARA": "ideal para",
}


def _is_sku_already_exists_error(exc: Exception) -> bool:
    """Detecta mensagens de conflito de SKU vindas da API ou do banco."""
    msg = str(exc or "").lower()
    return any(token in msg for token in [
        "sku already exists",
        "duplicate sku",
        "already exists",
        "já existe",
    ])


def validar_descricao(desc: str) -> bool:
    """Verifica se todas as seções fundamentais estão presentes na descrição."""
    desc_norm = normalizar_texto_validador(desc)
    tem_sobre = "sobre o produto" in desc_norm or len(desc_norm.split()) > 8
    tem_caract = any(k in desc_norm for k in ("principais caracteristicas", "caracteristicas", "diferenciais"))
    tem_benef = "beneficios" in desc_norm
    tem_duvidas = any(k in desc_norm for k in ("duvidas frequentes", "duvidas", "perguntas frequentes", "faq"))
    tem_ideal = any(k in desc_norm for k in ("ideal para", "indicado para", "recomendado para"))
    return all([tem_sobre, tem_caract, tem_benef, tem_duvidas, tem_ideal])


def normalizar_descricao(desc: str) -> str:
    """Garante que a descrição comece pelo conteúdo principal, sem lixo antes do início."""
    desc_strip = _limpar_travessoes(desc.strip())
    match = re.search(r'(?:[#*]*\s*)?(sobre\s+o\s+produto)(?:\s*[#*:]*)?', desc_strip, re.IGNORECASE)
    if match:
        idx = match.start()
        return desc_strip[idx:].strip()
    return desc_strip


def _ajustar_linha_item(linha: str, quantidade: int) -> str:
    """Ajusta o multiplicador de quantidade de uma linha de item do kit."""
    linha_limpa = linha.strip()
    if not linha_limpa:
        return linha

    match_marcador = re.match(r"^([\-\*\•]\s*|\d+[\.\)]\s*)", linha_limpa)
    marcador = match_marcador.group(1) if match_marcador else "• "
    conteudo = linha_limpa[len(marcador):].strip()

    if re.match(r"^\d+\s*[xX]\s+", conteudo):
        conteudo = re.sub(r"^\d+\s*[xX]\s+", "", conteudo).strip()
    elif re.search(r"\s+[xX]\s*\d+(?:\s+unidades?)?\s*$", conteudo, flags=re.IGNORECASE):
        conteudo = re.sub(r"\s+[xX]\s*\d+(?:\s+unidades?)?\s*$", "", conteudo, flags=re.IGNORECASE).strip()
    elif re.search(r"[\-\(]\s*\d+\s+unidades?\s*\)?\s*$", conteudo, flags=re.IGNORECASE):
        conteudo = re.sub(r"[\-\(]\s*\d+\s+unidades?\s*\)?\s*$", "", conteudo, flags=re.IGNORECASE).strip(" -(")
    elif re.search(r"\s+\d+\s+unidades?\s*$", conteudo, flags=re.IGNORECASE):
        conteudo = re.sub(r"\s+\d+\s+unidades?\s*$", "", conteudo, flags=re.IGNORECASE).strip()

    return f"{marcador}{conteudo} x {quantidade}"


def _injetar_secao_kit(texto: str, quantidade: int, nome_produto: str, nomes_secundarios: Optional[List[str]]) -> str:
    """Constrói e injeta a seção CONTEÚDO DA COMPRA caso ela não exista no texto."""
    prod_nome = nome_produto or "Produto"
    itens = [f"• {prod_nome} x {quantidade}"]
    for n_sec in (nomes_secundarios or []):
        if n_sec:
            itens.append(f"• {n_sec} x {quantidade}")

    bloco_kit = "CONTEÚDO DA COMPRA\n\n" + "\n".join(itens)
    if "IDEAL PARA" in texto:
        partes = re.split(r"(\bIDEAL PARA\b)", texto, maxsplit=1, flags=re.IGNORECASE)
        return f"{partes[0].strip()}\n\n{bloco_kit}\n\n{partes[1]}{partes[2]}"
    return f"{texto}\n\n{bloco_kit}"


def ajustar_descricao_quantidade(
    descricao: str,
    quantidade: int,
    nome_produto: str = "",
    nomes_secundarios: Optional[List[str]] = None,
) -> str:
    """Ajusta as quantidades dos itens na seção CONTEÚDO DA COMPRA ou O QUE VEM NO KIT."""
    if not descricao or quantidade < 1:
        return descricao or ""

    linhas, linhas_novas, em_secao, secao_encontrada = descricao.splitlines(), [], False, False
    re_kit = re.compile(
        r"^(?:[#*]*\s*)?(CONTE[ÚU]DO DA COMPRA|O QUE VEM NO KIT|ITENS INCLUSOS|CONTE[ÚU]DO DO PACOTE)(?:\s*[#*:]*)?$",
        re.I
    )
    re_outro = re.compile(
        r"^(?:[#*]*\s*)?(SOBRE O PRODUTO|PRINCIPAIS CARACTER[ÍI]STICAS|BENEF[ÍI]CIOS|MODO DE USO|DIFERENCIAIS|D[ÚU]VIDAS FREQUENTES|IDEAL PARA)(?:\s*[#*:]*)?$",
        re.I
    )

    for l in linhas:
        l_strip = l.strip()
        if re_kit.match(l_strip):
            em_secao, secao_encontrada = True, True
        elif em_secao and re_outro.match(l_strip):
            em_secao = False

        if em_secao and (l_strip.startswith(("-", "*", "•")) or re.match(r"^\d+[\.\)]", l_strip)):
            linhas_novas.append(_ajustar_linha_item(l, quantidade))
        else:
            linhas_novas.append(l)

    res = "\n".join(linhas_novas).strip()
    return res if secao_encontrada else _injetar_secao_kit(res, quantidade, nome_produto, nomes_secundarios)


# =============================================================================
# SEO GENERATOR (Orquestrador de Agentes Especializados)
# =============================================================================

class SEOGenerator:
    """
    Orquestrador de Agentes Especializados para SEO e Compliance.
    Usa Gemini 2.5 Flash para Compliance, Títulos e Descrição.
    Usa Llama 3.1/3.3 (Groq) para Ficha Técnica e Keywords.

    MUDANÇAS v2.1:
    - Alterado Títulos e Descrição para Gemini 2.5 Flash (Google AI Studio).
    - Temperatura 0.0 em todas as chamadas de geração.
    - Validação de caracteres feita 100% em Python.
    - Descrição usa template fixo com marcadores.
    """

    # -------------------------------------------------------------------------
    # AGENTE 1 — COMPLIANCE (Gemini)
    # -------------------------------------------------------------------------
    PROMPT_COMPLIANCE = """Você é um Auditor de Compliance Sênior do Mercado Livre Brasil com profundo conhecimento nas políticas de 2024/2025.
Sua única função é verificar se um produto pode ser anunciado na plataforma antes de qualquer geração de conteúdo.

====================================
CATEGORIAS PROIBIDAS - Bloqueie imediatamente
====================================

FINANCEIRO
- Moedas, cédulas antigas/comemorativas para venda como "investimento"
- Cheques, cartões clonados, maquinetas não homologadas
- Criptomoedas, tokens, NFTs

DIGITAL / SOFTWARE
- Contas de streaming, jogos ou plataformas (Netflix, Spotify, etc.)
- Senhas, acessos, convites
- Softwares piratas, chaves de ativação não oficiais
- Chips desbloqueados, IPTV pirata

SEGURANÇA E SAÚDE
- Armas de fogo, munições, simulacros funcionais
- Explosivos, fogos de artifício categoria 3 e 4
- Produtos controlados pela ANVISA sem registro válido
- Medicamentos sob prescrição, anabolizantes, hormônios
- Substâncias psicoativas, entorpecentes
- Equipamentos de vigilância para uso ilegal (grampos, rastreadores ocultos)

PROPRIEDADE INTELECTUAL
- Réplicas, falsificações, imitações de marcas registradas
- Produtos com termo "inspired by", "estilo de", "modelo igual ao"
- Itens sem nota fiscal de origem comprovável para marcas premium

NÃO-PRODUTO
- Serviços, consultorias, freelances, cursos (exceto se plataforma habilitada)
- Currículos, templates de currículo
- Promessas de resultado financeiro ou saúde

ADULTO
- Conteúdo sexual explícito fora da categoria adulto habilitada
- Produtos eróticos sem a categorização correta

ANIMAIS E MEIO AMBIENTE
- Espécies silvestres, tráfico de animais
- Produtos de origem animal sem certificado IBAMA
- Madeira não certificada

====================================
ATENÇÃO - Falsos Positivos Comuns (NÃO bloqueie)
====================================
- "Kit faca churrasco" -> PERMITIDO (utensílio doméstico)
- "Kit 2 copos Stanley" -> PERMITIDO (kit legítimo de produto permitido)
- "Kit 3 unidades faca de cozinha" -> PERMITIDO
- "Stanley original" -> PERMITIDO (marca legítima com palavra "original")
- "Garrafa térmica 500ml" -> PERMITIDO
- "Réplica design vintage" -> BLOQUEADO (termo réplica = falsificação)
- "Suplemento para emagrecer 10kg garantido" -> BLOQUEADO (promessa de resultado)
- "Canivete camping" -> PERMITIDO (ferramenta de uso legítimo)
- "Faca tática militar" -> AVALIAR CONTEXTO - se for utensílio de camping/pesca, permitido

====================================
INSTRUÇÃO DE ANÁLISE
====================================
1. Leia o nome e a descrição do produto
2. Verifique contra TODAS as categorias proibidas
3. Aplique o teste de falso positivo
4. Se houver dúvida genuína, classifique como "revisar" para análise humana

Responda EXCLUSIVAMENTE em JSON, sem texto adicional, sem markdown:

Se aprovado:
{"status": "ok", "observacao": "breve nota se relevante ou null"}

Se bloqueado:
{"status": "bloqueado", "motivo": "Explicação clara em 1 frase", "categoria": "Nome da categoria proibida"}

Se precisar de revisão humana:
{"status": "revisar", "motivo": "Motivo da dúvida em 1 frase", "categoria": "Categoria suspeita"}"""

    # -------------------------------------------------------------------------
    # AGENTE 2A — TÍTULO UNITÁRIO (Groq, temperatura 0.0)
    # -------------------------------------------------------------------------
    PROMPT_TITULO = """Você é um Especialista em SEO para Mercado Livre Brasil.
Sua única tarefa é gerar um ttulo neutro e descritivo para o produto fornecido.

====================================
ESTRUTURA RÍGIDA E OBRIGATÓRIA (NADA MAIS DEVE SER ACRESCENTADO)
====================================
Gere o ttulo rigorosamente seguindo apenas estes elementos na ordem abaixo:
1. [Quantidade] (apenas se for kit de mais de 1 unidade, ex: "Kit 2", "Kit 3". Para unitário, deixe vazio).
2. [Tipo de Produto] (categoria/nome objetivo do produto, ex: "Cola Vinil", "Tinta Spray", "Limpa Pedras", "Suporte TV").
3. [Formato / Especificação / Modelo / Compatibilidade / Aplicação Fática] (especificação útil real, ex: "250g", "17g", "Fixo 14 a 101 Polegadas", "para Metal e Madeira", "Uso Interno e Externo", "Secagem Rápida", "para Reboco"). Use "para" ou "compatível com" se fizer sentido.
4. [Marca] (APENAS se explicitamente informada no produto).

====================================
PROIBIÇÃO RIGOROSA DE ALUCINAÇÃO DE MARCAS:
====================================
- NUNCA invente, infira ou adicione marcas de terceiros (como "Colorart", "Artcolor", "Tekbond", "ChemiColor", "Sanol", etc.) se a marca NÃO constar explicitamente no nome ou atributos fornecidos do produto.
- Se a marca não for fornecida no produto de entrada, simplesmente OMITA o elemento [Marca] do título. É terminantemente proibido deduzir ou inventar marcas por associação com a categoria.

====================================
META DE COMPRIMENTO (APROVEITAMENTO MÁXIMO DE SEO NO MERCADO LIVRE):
====================================
- O título DEVE ter ENTRE 53 E 59 CARACTERES (meta ideal: 55 a 59 caracteres).
- EVITE RETORNAR TÍTULOS CURTOS (abaixo de 50 caracteres). Expanda o título adicionando especificações fáticas legítimas, superfícies compatíveis ("para Metal e Madeira"), finalidade técnica ("Uso Geral", "para Artesanato e Reparos") ou propriedades físicas reais ("Secagem Rápida", "Acabamento Metálico", "Bivolt", "Base Água").

====================================
REGRAS CRÍTICAS DE CONTRAPARTIÇÃO (EVITE E BLOQUEIE):
====================================
- O ttulo deve ter no MÁXIMO 60 caracteres.
- NUNCA use adjetivos ou termos comerciais/subjetivos ("Premium", "Clássico", "Forte", "Eficaz", "Poderoso", "Potente", "Excelente", "Original", "Garantia", "Envio Rápido", "Resistente", "Prático", "Alta Qualidade", etc.).
- NUNCA use promessas de resultado, cura, emagrecimento ou tratamento.
- NUNCA use repetições, emojis, pontuação excessiva ou caracteres especiais (- , . ! ? % & @ # / [ ] ( )).
- Primeira letra maiúscula de cada palavra, restante em minúsculo (exceto siglas).

Responda EXCLUSIVAMENTE em JSON:
{"titulo": "ttulo aqui"}"""

    # -------------------------------------------------------------------------
    # AGENTE 2B — GRADE DE 8 TÍTULOS (Groq, temperatura 0.0)
    # -------------------------------------------------------------------------
    PROMPT_TITULO_KITS = """Você é um Especialista em SEO para Mercado Livre Brasil.
Sua tarefa é gerar 8 ttulos base únicos e altamente otimizados seguindo estritamente a estrutura neutra e descritiva.

====================================
ESTRUTURA RÍGIDA E OBRIGATÓRIA (NADA MAIS DEVE SER ACRESCENTADO)
====================================
Gere os ttulos base rigorosamente com base nos elementos abaixo:
1. [Tipo de Produto] (categoria/nome objetivo do produto, ex: "Cola Vinil", "Tinta Spray", "Limpa Pedras", "Suporte TV").
2. [Formato / Especificação / Modelo / Compatibilidade / Aplicação Fática] (especificação útil real, ex: "250g", "17g", "Fixo 14 a 101 Polegadas", "para Metal e Madeira", "Uso Interno e Externo", "Secagem Rápida").
3. [Marca] (APENAS se explicitamente informada no produto).

*ATENÇÃO: NÃO inclua quantidades (ex: "Kit 2", "3 Unidades") na grade de ttulos. O sistema injetará as quantidades automaticamente no final.

====================================
PROIBIÇÃO RIGOROSA DE ALUCINAÇÃO DE MARCAS:
====================================
- NUNCA invente, infira ou adicione marcas de terceiros (como "Colorart", "Artcolor", "Tekbond", "ChemiColor", "Sanol", etc.) se a marca NÃO constar explicitamente no nome ou atributos fornecidos do produto.
- Se a marca não for fornecida no produto de entrada, simplesmente OMITA o elemento [Marca] do título. É terminantemente proibido deduzir ou inventar marcas por associação com a categoria.

====================================
META DE COMPRIMENTO BASE (APROVEITAMENTO MÁXIMO DE SEO):
====================================
- Cada título BASE deve ter ENTRE 45 E 52 CARACTERES. Como o sistema injetará "Kit N " (ex: "Kit 4 " = +6 chars) no início, o título final terá EXATAMENTE 53 a 58 caracteres.
- NUNCA gere títulos base muito curtos (abaixo de 42 caracteres). Expanda a variação de cada título adicionando especificações fáticas legítimas, superfícies compatíveis ("para Metal e Madeira", "para Alvenaria"), finalidade de uso ("Uso Geral", "para Artesanato") ou características físicas ("Secagem Rápida", "Acabamento Metálico", "Bivolt", "Massa 250g").

====================================
REGRAS CRÍTICAS (EVITE E BLOQUEIE):
====================================
- NUNCA use adjetivos ou termos comerciais/subjetivos ("Premium", "Clássico", "Forte", "Eficaz", "Poderoso", "Potente", "Excelente", "Original", "Garantia", "Envio Rápido", "Resistente", "Prático", etc.).
- NUNCA use promessas de resultado, cura, tratamento ou performance.
- NENHUM caractere especial ou pontuação excessiva.

Responda EXCLUSIVAMENTE em JSON. Você DEVE escrever a 'estrategia_seo' antes de gerar os 'titulos' para ancorar o contexto:
{
  "estrategia_seo": "Palavras-chave e especificações reais do produto.",
  "titulos": {
    "t1_a": "Ttulo 1...",
    "t1_b": "Ttulo 2...",
    "t2_a": "Ttulo 3...",
    "t2_b": "Ttulo 4...",
    "t3_a": "Ttulo 5...",
    "t3_b": "Ttulo 6...",
    "t4_a": "Ttulo 7...",
    "t4_b": "Ttulo 8..."
  }
}"""


    # -------------------------------------------------------------------------
    # AGENTE 4 — FICHA TÉCNICA / ATRIBUTOS (Groq)
    # -------------------------------------------------------------------------
    PROMPT_TECH = """Você é um Analista de Dados Técnicos especialista em fichas de produto
para e-commerce e marketplaces brasileiros. Sua função é extrair, inferir
e padronizar atributos técnicos a partir de qualquer tipo de texto sobre
produtos (título, descrição, bullet points, nome de arquivo, SKU, etc.).

====================================
ETAPA 0 — DETECÇÃO DE MODELO
====================================
Antes de preencher os campos solicitados, identifique o MODELO do produto.
O modelo é o identificador específico que diferencia variantes de uma mesma
linha (ex: "Classic 1.0", "AX-500", "Pro Max 64GB").

Estratégias de detecção (aplique nesta ordem):

  A) CÓDIGO ALFANUMÉRICO no título ou SKU
     Padrão: sequência com letras + números separados por hífen, ponto ou
     espaço após a marca.
     Exemplos:
       "Stanley ADVC-10" → Modelo: "ADVC-10"
       "Tramontina 29899-083" → Modelo: "29899-083"
       "Nike Air Max 90" → Modelo: "Air Max 90"

  B) NOME DE LINHA / SÉRIE explícito
     Palavras que denotam série após a marca ou categoria.
     Exemplos:
       "Garrafa Stanley Classic" → Modelo: "Classic"
       "Faca Tramontina Century" → Modelo: "Century"

  C) ESPECIFICAÇÃO TÉCNICA IDENTIFICADORA
     Quando o modelo é descrito só por capacidade/tamanho e não há nome.
     Exemplos:
       "Garrafa Inox 750ml" (sem código) → Modelo: "750 ml (inferido)"

  D) NÃO IDENTIFICÁVEL
     Se nenhuma estratégia encontrar o modelo → omita a chave "Modelo"
     (ou use "Não informado" se marcado como [OBRIGATÓRIO]).

Sempre inclua "Modelo" como primeiro campo do JSON de atributos.

====================================
ETAPA 1 — HIERARQUIA DE PREENCHIMENTO
====================================
Para cada campo da lista "Campos Solicitados":

  1. EXPLÍCITO  → valor mencionado diretamente no texto.
                  Extraia e padronize conforme seção PADRONIZAÇÃO.

  2. INFERÍVEL  → dedutível com alta confiança a partir do contexto.
                  Acrescente " (inferido)" ao valor.
                  EXCEÇÃO: campos com lista de Valores Permitidos →
                  escolha o valor mais adequado da lista SEM sufixo.

  3. ENRIQUECÍVEL → você conhece o produto/marca e pode preencher com
                  conhecimento de mercado (ex: marca conhecida, voltagem
                  padrão do segmento).
                  Acrescente " (conhecimento de mercado)" ao valor.
                  EXCEÇÃO: mesma regra das listas de valores permitidos.

  4. AUSENTE    → OMITA COMPLETAMENTE a chave do JSON. NUNCA use "Não informado", "N/A", "null" ou "PREENCHER".

Prioridade de confiança: Explícito > Inferível (apenas se óbvio) > Ausente (omitir chave).

====================================
ETAPA 2 — VALIDAÇÃO ANTES DE RETORNAR
====================================
Antes de gerar o JSON final, faça internamente estas verificações:

  ✓ O campo "Modelo" está presente e é o primeiro?
  ✓ Cada chave bate exatamente com o nome em "Campos Solicitados"?
  ✓ Campos com Valores Permitidos usam exatamente um valor da lista?
  ✓ Nenhum campo contém null, "", "N/A", "Desconhecido" ou similar?
  ✓ Campos ausentes sem [OBRIGATÓRIO] foram omitidos (não retornados)?
  ✓ Medidas têm número + espaço + unidade?
  ✓ Não há EAN, GTIN ou Código de barras no JSON?

====================================
REGRAS CRÍTICAS
====================================
R1. Use EXATAMENTE os nomes de atributos fornecidos em "Campos Solicitados".
R2. Campos com "Valores Permitidos": escolha EXATAMENTE um valor da lista.
    Nunca combine, abrevie ou invente termos fora da lista.
R3. Conflito texto × lista → priorize a lista (valor mais próximo
    semanticamente).
R4. NUNCA extraia: "Código de barras", "EAN", "GTIN" ou "Motivo de GTIN
    vazio". O sistema preenche a identificação automaticamente via API.
R5. VOCÊ É UM EXTRATOR RIGOROSO DE DADOS. Extraia valores baseando-se EXCLUSIVAMENTE no texto fornecido. Se a informação não estiver explicitamente presente ou for indiscutível no texto, OMITA a chave. NUNCA adivinhe. NUNCA deduza. NUNCA invente.

====================================
PADRONIZAÇÃO DE VALORES
====================================
- Idioma       : Português brasileiro.
                 ✓ "Preto"  ✗ "Black" | ✓ "Azul marinho"  ✗ "Navy"
- Medidas      : número + espaço + unidade → "500 mL" (Volume: SEMPRE 'mL' com L maiúsculo), "15 cm" (Dimensões: SEMPRE número inteiro sem ponto ou vírgula, ex: 15 cm, 7 cm), "2 kg"
- Booleanos    : "Sim" ou "Não"
- Voltagem     : "110V" | "220V" | "Bivolt" | "Não se aplica"
- Marcas       : capitalização correta → "Stanley", "3M", "Tramontina"
- Cores        : primeira letra maiúscula → "Preto", "Inox", "Chumbo"
- Modelo       : preserve o código original (maiúsculas, hífens, pontos)

====================================
EXEMPLO COMPLETO
====================================
Campos Solicitados:
  - Material [OBRIGATÓRIO]
    (Valores Permitidos: "Inox", "Plástico", "Vidro", "Alumínio")
  - Capacidade [OBRIGATÓRIO]
  - Cor
  - Voltagem
  - Peso

Texto de entrada:
  "Garrafa Térmica Stanley Classic 1.0 Aço Inox 750ml Grafite ADVC-10"

Saída esperada:
{
  "atributos": {
    "Modelo": "ADVC-10",
    "Material": "Inox",
    "Capacidade": "750 ml",
    "Cor": "Grafite",
    "Voltagem": "Não se aplica",
    "Peso": "Não informado"
  }
}

Notas do exemplo:
- "Modelo" detectado pela estratégia A (código alfanumérico "ADVC-10").
- "Material" veio da lista → sem sufixo.
- "Cor" estava explícita → sem sufixo.
- "Voltagem" preenchida por conhecimento de mercado (garrafa = sem
  elétrica) → valor da lista, sem sufixo.
- "Peso" ausente + [OBRIGATÓRIO] → "Não informado".
- "Classic 1.0" ignorado no Modelo pois "ADVC-10" é mais específico
  (estratégia A tem prioridade sobre B).

====================================
FORMATO DE SAÍDA
====================================
Responda EXCLUSIVAMENTE em JSON válido, sem texto adicional,
sem markdown, sem comentários:
{
  "atributos": {
    "Modelo": "...",
    "NOME_EXATO_DO_CAMPO": "Valor Padronizado"
  }
}"""

    # -------------------------------------------------------------------------
    # AGENTE 5 — PALAVRAS-CHAVE / SEO (Groq)
    # -------------------------------------------------------------------------
    PROMPT_KEYWORDS = """Você é um Especialista em Search Intelligence para marketplaces brasileiros com foco em Mercado Livre e Google Shopping.
Sua função é identificar os termos de busca mais relevantes para maximizar o ranqueamento orgânico do anúncio.

====================================
PRINCÍPIOS DO ALGORITMO DO ML
====================================
O Mercado Livre ranqueia com base em:
1. Relevância do título com o termo buscado
2. Velocidade de vendas e taxa de conversão
3. Reputação do vendedor
4. Qualidade e completude do anúncio
5. Palavras-chave presentes na descrição

====================================
LÓGICA DE KITS - OBRIGATÓRIO
====================================
Para kits com 2, 3 ou 4 unidades, inclua obrigatoriamente:
- "kit 2 [produto]", "kit 3 [produto]", "kit 4 [produto]"
- "conjunto 2 [produto]", "conjunto 3 [produto]"
- "par de [produto]" quando kit 2 fizer sentido
- O produto unitário também (base para variações de busca)

====================================
CATEGORIAS DE TERMOS - Gere para cada uma
====================================

TERMOS PRIMÁRIOS (alta intenção de compra)
- Como o comprador pesquisa o produto principal
- Marca + produto quando a marca for relevante para busca

TERMOS SECUNDÁRIOS (long tail, menor concorrência)
- Com atributos: material, capacidade, cor, tamanho
- Combinações de 3-4 palavras

TERMOS DE KIT (se aplicável)
- "kit N [produto]", "conjunto N [produto]", "N unidades [produto]"

TERMOS ALTERNATIVOS (como o público leigo pesquisa)
- Sinônimos, nomes populares, variações ortográficas relevantes

====================================
REGRAS DE QUALIDADE
====================================
- Todos os termos em letras minúsculas
- Sem pontuação ou símbolos
- Sem termos genéricos vazios: "produto bom", "qualidade ótima"
- Sem termos proibidos: preço, frete, garantia, promoção
- Máximo 4 palavras por termo
- Mínimo 12 termos, máximo 25

Analise o produto abaixo e gere a lista de termos.
Responda EXCLUSIVAMENTE em JSON, sem texto adicional, sem markdown:
{
  "termos_primarios": ["termo1", "termo2"],
  "termos_secundarios": ["termo1", "termo2"],
  "termos_kit": ["kit 2 produto", "kit 3 produto"],
  "termos_alternativos": ["termo1", "termo2"],
  "tipo": "unitario|kit",
  "quantidade_kit": null
}"""

    # -------------------------------------------------------------------------
    # AGENTE 6 — COPYWRITER / DESCRIÇÃO (Gemini 2.5 Flash)
    # -------------------------------------------------------------------------
    PROMPT_COPY = """Você é um Copywriter Profissional de E-commerce de Alto Nível especialista em conversão no Mercado Livre Brasil.
Sua missão é criar uma descrição clara, altamente escaneável no celular, técnica e persuasiva para o produto fornecido.

====================================
DIRETRIZES FUNDAMENTAIS (LEITURA RÁPIDA E CONVERSÃO)
====================================
1. SOBRE O PRODUTO DEVE SER ENXUTO: No máximo 2 parágrafos curtos (3 a 5 linhas cada). Vá direto ao ponto, sem enrolação ou textos longos que cansem o leitor.
2. PROIBIÇÃO ABSOLUTA DE TRAVESSÕES (—, –, --): NUNCA use travessões em nenhuma parte do texto. Use dois-pontos (:), hífen simples (-) ou marcadores com bolinha (•).
3. USE DADOS REAIS: Integre as dimensões exatas, materiais, composição e características extraídas das imagens da embalagem ou catálogo.
4. MODO DE USO: Apresente um passo a passo numerado, prático e objetivo quando aplicável ao produto.
5. DÚVIDAS FREQUENTES: Traga 3 perguntas reais numeradas com respostas curtas e objetivas que eliminam dúvidas de compra.
6. CONTEÚDO DA COMPRA: Liste exatamente o que acompanha a compra (ex: • 1x [Nome do Produto com especificação] ou • Kit com [Qtd]x [Nome]).
7. REGRA DE QUANTIDADES: Sempre que precisar preencher quantidade de itens (em CONTEÚDO DA COMPRA ou Ficha Técnica), consulte obrigatoriamente o campo responsável de quantidade (`quantidade`, `qtd_kit`, `unidades_por_kit`). NUNCA insira '1 unidade' como valor padrão genérico.

====================================
TEMPLATE OBRIGATÓRIO (Mantenha EXATAMENTE esta estrutura de cabeçalhos)
====================================
SOBRE O PRODUTO
[Parágrafo 1 curto: Apresente o produto e sua principal função e praticidade de forma direta.]

[Parágrafo 2 curto: Destaque o acabamento, material ou tecnologia e onde pode ser utilizado.]

PRINCIPAIS CARACTERÍSTICAS
• [Formato / Modelo]
• [Cor / Acabamento]
• [Dimensões / Medidas exatas]
• [Material / Composição]
• [Outras especificações técnicas comprovadas]

BENEFÍCIOS
• [Verbo de ação no presente + resultado prático]
• [Verbo de ação no presente + resultado prático]
• [Verbo de ação no presente + resultado prático]
• [Verbo de ação no presente + resultado prático]

MODO DE USO
1. [Passo 1 de aplicação ou uso]
2. [Passo 2 de aplicação ou uso]
3. [Passo 3 de aplicação ou uso]

CONTEÚDO DA COMPRA
• 1x [Nome do Produto com especificação principal]

DÚVIDAS FREQUENTES
1. [Pergunta comum de comprador?]
R: [Resposta curta, direta e confiante]

2. [Pergunta comum de comprador?]
R: [Resposta curta, direta e confiante]

3. [Pergunta comum de comprador?]
R: [Resposta curta, direta e confiante]

IDEAL PARA
[Texto curto ou lista de superfícies, móveis, ambientes ou cenários reais onde o produto é indicado.]

INFORMAÇÕES IMPORTANTES
[Inclua somente se houver registro ANVISA, INMETRO ou advertência mandatória. Caso contrário, omita este cabeçalho.]

====================================
DIRETRIZES DE ESTILO
====================================
- Proibido clichês: "Melhor do mercado", "Qualidade garantida", "Preço imbatível", "Não perca", "Compre agora".
- NUNCA inclua números soltos de código de barras (EAN/SKU) no texto da descrição.
- Texto 100% limpo, sem markdown complexo além de quebras de linha e bullets.

Responda EXCLUSIVAMENTE em JSON, sem nenhum texto adicional:
{
  "descricao": "Texto completo da descrição com quebras de linha \\n"
}"""

    MAX_NOME = 60

    def __init__(self, gemini: Optional[GeminiClient] = None, groq: Optional[GroqClient] = None) -> None:
        self.gemini = gemini or GeminiClient()
        self.groq   = groq or GroqClient()

    # -------------------------------------------------------------------------
    # Método principal
    # -------------------------------------------------------------------------

    def _extrair_evidencias_imagens(self, imagens_urls: Optional[List[str]]) -> str:
        """Extrai textos da embalagem, medidas e dados visuais via Gemini Vision multimodal."""
        if not imagens_urls:
            return ""
        try:
            partes_imagens = [
                img for url in imagens_urls[:3]
                if url and isinstance(url, str) and url.startswith("http")
                if (img := baixar_imagem_para_base64(url))
            ]
            if not partes_imagens:
                return ""

            prompt_ocr = (
                "Analise as imagens da embalagem do produto e extraia apenas fatos visíveis:\n"
                "1. Textos da embalagem (marca, modelo, slogan técnico)\n"
                "2. Dimensões, medidas, peso, volume e quantidade de peças na embalagem\n"
                "3. Modo de uso / aplicação descrito no verso da embalagem\n"
                "4. Superfícies indicadas, advertências e composição do material\n"
                "Responda em tópicos curtos e objetivos."
            )
            res = self.gemini.chat_multimodal(
                system_prompt="Você é um assistente de visão computacional para e-commerce.",
                user_input=prompt_ocr,
                imagens=partes_imagens,
                modelo="gemini-2.5-flash",
                use_cache=True,
            )
            return res.strip() if res else ""
        except Exception as exc:
            logger.warning("Falha ao extrair evidências visuais das imagens: %s", exc)
            return ""

    def _pesquisar_concorrentes_ml(
        self,
        nome_produto: str,
        ean: str = "",
        marca: str = "",
        category_id: str = "",
    ) -> str:
        """Busca dados de catálogo e especificações de concorrentes no Mercado Livre."""
        try:
            from integracoes.mercadolivre.client import MercadoLivreClient
            ml_client = MercadoLivreClient()
            atribs = ml_client.buscar_atributos_catalogo_ml(
                ean=ean, termo_busca=nome_produto, marca=marca, category_id=category_id
            )
            if not atribs:
                return ""
            itens = [f"{k}: {v}" for k, v in atribs.items() if v and str(v).lower() != "null"]
            return "Especificações do Catálogo ML:\n" + "\n".join(itens[:10])
        except Exception as exc:
            logger.warning("Falha ao pesquisar concorrentes/catálogo no ML: %s", exc)
            return ""

    def gerar_conteudo(
        self,
        nome_original: str,
        descricao_atual: str = "",
        features: Optional[Dict] = None,
        qtd_kit: int = 1,
        e_misto: bool = False,
        atributos_obrigatorios: Optional[List[str]] = None,
        gera_grade: bool = False,
        category_id_ml: Optional[str] = None,
        imagens_urls: Optional[List[str]] = None,
        ean: str = "",
        marca: str = "",
    ) -> Dict:
        import json
        import re

        if not nome_original.strip():
            return {"titulo": "", "descricao": descricao_atual or "", "atributos": {}}

        # Limpa códigos EAN/SKU ou barras do início do nome
        nome_limpo = re.sub(r'^[\d\s\|\-]+', '', nome_original).strip()
        if not nome_limpo:
            nome_limpo = nome_original

        contexto = f"Produto: {nome_limpo}\nQtd: {qtd_kit} unidades\nOriginal: {descricao_atual[:2000]}"
        if features:
            contexto += f"\nAtributos: {str(features)[:300]}"

        # --- AGENTE 1: COMPLIANCE (Gemini) ---
        logger.info(f"[Agente 1] Compliance: {nome_original[:50]}...")
        res_comp = self.gemini.chat(system_prompt=self.PROMPT_COMPLIANCE, user_input=contexto)
        if not res_comp:
            raise ValueError("Serviço de Compliance indisponível.")

        try:
            data_comp = json.loads(res_comp.strip())
            if data_comp.get("status") == "bloqueado":
                logger.warning(f"Compliance BLOQUEIO: {data_comp.get('motivo')}")
                raise ValueError(f"Produto violou politicas: {data_comp.get('motivo')}")
            if data_comp.get("status") == "revisar":
                logger.warning(f"Compliance REVISAR: {data_comp.get('motivo')}")
        except json.JSONDecodeError:
            logger.error(f"Erro JSON Compliance: {res_comp}")

        # --- AGENTE 2: TÍTULOS (Gemini 2.5 Flash) ---
        if gera_grade:
            titulo, titulos_grade = self._gerar_grade_titulos(contexto, nome_original)
        else:
            titulo, titulos_grade = self._gerar_titulo_unitario(contexto, nome_original)

        # --- EVIDÊNCIAS VISUAIS + PESQUISA CONCORRENTES ML ---
        marca_ctx = str(marca or (features or {}).get("Marca") or (features or {}).get("marca") or "")
        ean_ctx   = str(ean or (features or {}).get("EAN") or (features or {}).get("ean") or (features or {}).get("Código universal de produto") or "")
        
        logger.info("[Agente Visão] Extraindo evidências das imagens da embalagem...")
        evidencias_visuais = self._extrair_evidencias_imagens(imagens_urls)
        
        logger.info("[Agente Concorrentes] Pesquisando referências e catálogo no Mercado Livre...")
        dados_concorrentes = self._pesquisar_concorrentes_ml(nome_limpo, ean=ean_ctx, marca=marca_ctx, category_id=category_id_ml or "")

        # --- AGENTE 6: DESCRIÇÃO (Gemini 2.5 Flash) ---
        logger.info("[Agente 3] Gerando Descrição Copywriter (Gemini 2.5 Flash)...")
        desc = self._gerar_descricao(titulo, contexto, descricao_atual, evidencias_visuais, dados_concorrentes)

        # --- AGENTE 4: FICHA TÉCNICA (Catálogo ML + Groq Restrito) ---
        logger.info("[Agente 4] Extraindo Ficha Técnica (Catálogo ML + Groq Restrito)...")
        atribs = self._gerar_ficha_tecnica(contexto, atributos_obrigatorios, ean=str(ean_ctx), marca=str(marca_ctx), category_id_ml=category_id_ml or "")

        # --- AGENTE 5: PALAVRAS-CHAVE (Groq) ---
        logger.info("[Agente 5] Identificando Palavras-chave SEO (Groq)...")
        keywords_data = self._gerar_keywords(titulo, contexto)

        # Validação de termos proibidos
        v_nome = TermosProibidos.verificar(titulo)
        if v_nome["bloqueado"]:
            raise ValueError(f"Título proibido: {v_nome['termo']}")

        # Formatação final do título
        titulo = formatar_title_case(titulo)

        return {
            "titulo":        (titulo or nome_original)[:self.MAX_NOME],
            "titulos_grade": titulos_grade,
            "descricao":     desc or descricao_atual,
            "atributos":     atribs,
            "keywords":      keywords_data,
        }

    # -------------------------------------------------------------------------
    # Submétodos internos
    # -------------------------------------------------------------------------

    def _gerar_titulo_unitario(self, contexto: str, nome_original: str):
        """
        Gera um título unitário via Gemini 2.5 Flash.
        A validação de caracteres é feita 100% em Python.
        """
        import json

        titulo = ""
        titulos_grade = {}
        max_tentativas = 3
        feedback_erro = ""

        for tentativa in range(max_tentativas):
            input_prompt = contexto
            if feedback_erro:
                input_prompt += f"\n\nATENÇÃO: A tentativa anterior gerou um título com {feedback_erro}. Ajuste o comprimento e tente novamente."

            res_tit = self.gemini.chat(
                system_prompt=self.PROMPT_TITULO,
                user_input=input_prompt,
                modelo="gemini-2.5-flash",
                use_cache=(tentativa == 0),
            )

            try:
                if not res_tit:
                    logger.warning(f"Resposta vazia na tentativa {tentativa + 1}.")
                    break

                data_tit = json.loads(res_tit.strip())
                t_temp = formatar_title_case(str(data_tit.get("titulo", "")).strip())

                tamanho = len(t_temp)
                if 55 <= tamanho <= 60:
                    titulo = t_temp
                    logger.info(f"Título unitário aprovado ({tamanho} chars) na tentativa {tentativa + 1}: {titulo}")
                    break
                else:
                    feedback_erro = f"{tamanho} caracteres (precisa de 55 a 60)"
                    logger.warning(f"Tentativa {tentativa + 1}: {feedback_erro}")

                    # Na última tentativa: aplica ajuste por Python
                    if tentativa == max_tentativas - 1:
                        titulo = ajustar_titulo(t_temp)
                        logger.warning(f"Fallback Python aplicado: {titulo!r} ({len(titulo)} chars)")

            except Exception as e:
                feedback_erro = f"JSON inválido: {e}"
                logger.warning(f"Tentativa {tentativa + 1}: {feedback_erro}")

        if not titulo:
            titulo = ajustar_titulo(formatar_title_case(nome_original))

        return titulo, titulos_grade

    def _gerar_grade_titulos(self, contexto: str, nome_original: str):
        """
        Gera a grade de 8 títulos via Gemini 2.5 Flash.
        A IA gera apenas a base otimizada. O Python mapeia e finaliza.
        """
        import json

        titulos_grade = {}
        max_tentativas = 3
        feedback_erro = ""

        for tentativa in range(max_tentativas):
            input_prompt = contexto
            if feedback_erro:
                input_prompt += f"\n\nATENÇÃO — Erros na tentativa anterior:\n{feedback_erro}\nCorrija no próximo JSON."

            res_tit = self.gemini.chat(
                system_prompt=self.PROMPT_TITULO_KITS,
                user_input=input_prompt,
                modelo="gemini-2.5-flash",
                use_cache=(tentativa == 0),
            )

            try:
                if not res_tit:
                    logger.warning(f"Resposta vazia na tentativa {tentativa + 1}.")
                    break

                data_tit = json.loads(res_tit.strip())
                titulos_brutos = data_tit.get("titulos", {})

                # Mapeamento das chaves neutras da IA para as chaves do seu sistema
                mapa_chaves = {
                    "t1_a": "1_classico",
                    "t1_b": "1_premium",
                    "t2_a": "2_classico",
                    "t2_b": "2_premium",
                    "t3_a": "3_classico",
                    "t3_b": "3_premium",
                    "t4_a": "4_classico",
                    "t4_b": "4_premium",
                }

                titulos_temp = {}
                curtos = []
                for chave_ia, chave_sistema in mapa_chaves.items():
                    texto_titulo = str(titulos_brutos.get(chave_ia, "")).strip()
                    fmt_t = formatar_title_case(texto_titulo)
                    titulos_temp[chave_sistema] = fmt_t
                    if fmt_t and len(fmt_t) < 42:
                        curtos.append(f"{chave_ia} ({len(fmt_t)} chars)")

                # Verifica se todos os 8 títulos foram gerados e não estão excessivamente curtos
                faltantes = [k for k, v in titulos_temp.items() if not v]
                if not faltantes and not curtos:
                    titulos_grade = titulos_temp
                    logger.info(f"Grade aprovada na tentativa {tentativa + 1}. Estratégia SEO utilizada: {data_tit.get('estrategia_seo', 'Não informada')}")
                    break
                else:
                    erros = []
                    if faltantes:
                        erros.append(f"faltantes no JSON: {faltantes}")
                    if curtos:
                        erros.append(f"muito curtos (precisam ter entre 45 e 52 caracteres base): {curtos}")
                    feedback_erro = "; ".join(erros)
                    logger.warning(f"Falha na tentativa {tentativa + 1}: {feedback_erro}")
                    # Na última tentativa, se a grade estiver completa, aceita o melhor resultado disponível
                    if tentativa == max_tentativas - 1 and titulos_temp and not faltantes:
                        titulos_grade = titulos_temp

            except Exception as e:
                feedback_erro = f"JSON inválido: {e}"
                logger.warning(f"Tentativa {tentativa + 1}: {feedback_erro}")

        # Fallback de segurança 100% Python
        if not titulos_grade:
            base = formatar_title_case(nome_original)
            titulos_grade = {
                "1_classico": base,
                "1_premium":  base,
                "2_classico": base,
                "2_premium":  base,
                "3_classico": base,
                "3_premium":  base,
                "4_classico": base,
                "4_premium":  base,
            }

        titulo = titulos_grade.get("1_classico") or formatar_title_case(nome_original)
        return titulo, titulos_grade

    def _parse_descricao_json(self, res_copy: str) -> str:
        """
        Faz o parse robusto do JSON de descrição retornado pela IA.
        Trata chaves faltantes, quebras de linha literais, aspas não escapadas e truncamento.
        """
        import json
        import re

        res_clean = res_copy.strip()

        # 1. Tentativa padrão com json.loads
        try:
            data = json.loads(res_clean)
            if isinstance(data, dict) and "descricao" in data:
                return str(data["descricao"])
        except Exception:
            pass

        # 2. Tentativa com strict=False (permite quebras de linha reais/caracteres de controle)
        try:
            data = json.loads(res_clean, strict=False)
            if isinstance(data, dict) and "descricao" in data:
                return str(data["descricao"])
        except Exception:
            pass

        # 3. Extração via Regex (super robusta contra aspas/quebras/truncamento)
        # Remove tags markdown do bloco json se houver
        if res_clean.startswith("```"):
            res_clean = re.sub(r'^```(?:json)?\n', '', res_clean)
            res_clean = re.sub(r'\n```$', '', res_clean).strip()

        # Procura a chave "descricao" ou "descriçao" ou "descrição"
        match = re.search(r'"descri[cç]ao"\s*:\s*"(.*)', res_clean, re.DOTALL)
        if match:
            content = match.group(1).strip()
            
            # Limpa fechamentos do JSON no fim do conteúdo se existirem
            if content.endswith('}'):
                content = content[:-1].strip()
            if content.endswith('"'):
                content = content[:-1]
                
            # Substitui escapes comuns
            content = content.replace('\\n', '\n')
            content = content.replace('\\"', '"')
            content = content.replace('\\\\', '\\')
            return content.strip()

        # 4. Fallback extremo: limpa o texto bruto se não encontrar chaves
        fallback_text = res_clean
        if fallback_text.startswith('{') and fallback_text.endswith('}'):
            fallback_text = fallback_text[1:-1].strip()
        if fallback_text.startswith('"') and fallback_text.endswith('"'):
            fallback_text = fallback_text[1:-1].strip()
            
        fallback_text = fallback_text.replace('\\n', '\n').replace('\\"', '"')
        return fallback_text.strip()

    def _gerar_descricao(
        self,
        titulo: str,
        contexto: str,
        descricao_atual: str,
        evidencias_visuais: str = "",
        dados_concorrentes: str = "",
    ) -> str:
        """Gera descrição via Gemini 2.5 Flash aplicando validação e sanitização rigorosa."""
        desc, feedback_erro = "", ""
        ctx_completo = contexto
        if evidencias_visuais:
            ctx_completo += f"\n\nEVIDÊNCIAS VISUAIS DA EMBALAGEM (OCR):\n{evidencias_visuais}"
        if dados_concorrentes:
            ctx_completo += f"\n\nDADOS DE CATÁLOGO / CONCORRENTES:\n{dados_concorrentes}"

        for tentativa in range(3):
            input_prompt = f"Título Gerado: {titulo}\n{ctx_completo}"
            if feedback_erro:
                input_prompt += f"\n\nATENÇÃO — Erros na tentativa anterior:\n{feedback_erro}\nCorrija no próximo JSON."

            res_copy = self.gemini.chat(
                system_prompt=self.PROMPT_COPY,
                user_input=input_prompt,
                modelo="gemini-2.5-flash",
                use_cache=(tentativa == 0),
            )
            if not res_copy:
                feedback_erro = "Resposta vazia. Retorne o JSON completo."
                continue

            try:
                desc_temp = _limpar_travessoes(normalizar_descricao(self._parse_descricao_json(res_copy)))
                if validar_descricao(desc_temp):
                    desc = desc_temp
                    logger.info("Descrição aprovada (%d chars) na tentativa %d.", len(desc), tentativa + 1)
                    break

                desc_norm = normalizar_texto_validador(desc_temp)
                faltantes = [s for s, n in SECOES_OBRIGATORIAS_NORM.items() if n not in desc_norm]
                feedback_erro = f"Faltaram as seções obrigatórias: {faltantes}"
                if tentativa == 2:
                    desc = desc_temp
            except Exception as e:
                logger.warning("Falha no parse da Descrição (tentativa %d): %s", tentativa + 1, e)
                feedback_erro = f"Erro de processamento: {e}"

        return _limpar_travessoes(desc or descricao_atual)

    def _gerar_ficha_tecnica(self, contexto: str, atributos_obrigatorios: Optional[List[str]], ean: str = "", marca: str = "", category_id_ml: str = "", produto_id: str = "0", sku: str = "", dados_internos: Optional[Dict] = None, quantidade: int = 1) -> Dict:
        """
        Orquestra a Ficha Técnica Inteligente através do FichaTecnicaOrchestrator:
        Pipeline de 9 passos: Encontrar → Extrair → Comprovar → Normalizar → Validar → Auditar (Gemini 100%) → Corrigir/Bloquear → Publicar.
        """
        if not atributos_obrigatorios:
            atributos_obrigatorios = []

        try:
            from criacao_estruturado.services.ficha_tecnica import FichaTecnicaOrchestrator
            orchestrator = FichaTecnicaOrchestrator()
            features_finais, relatorio = orchestrator.gerar_e_auditar_ficha_tecnica(
                contexto=contexto,
                atributos_obrigatorios=atributos_obrigatorios,
                ean=ean,
                marca=marca,
                category_id_ml=category_id_ml,
                produto_id=str(produto_id),
                sku=str(sku),
                dados_internos=dados_internos,
                quantidade=quantidade
            )
            return features_finais
        except Exception as e_orch:
            logger.warning("[CriadorKitsService] Falha no orquestrador da Ficha Técnica: %s — aplicando fallback de sanitização.", e_orch)
            return {}

    @staticmethod
    def _padronizar_atributos_features(features: Dict) -> Dict:
        """
        Padroniza e purga valores de atributos nas features:
        1. Elimina completamente chaves com valores ausentes ou placeholders ('Não informado', 'N/A', 'PREENCHER', etc.).
        2. Formata unidades de volume de 'ml'/'Ml'/'ML' para 'mL' (ex: '350 mL', 'mL').
        3. Garante que dimensões (altura, largura, comprimento, profundidade) sejam números inteiros sem ponto ou vírgula (ex: '7 cm').
        """
        import re

        if not isinstance(features, dict):
            return features

        PROIBIDOS = {
            "não informado", "nao informado", "a informar", "desconhecido", 
            "null", "n/a", "preencher", "não se aplica", "nao se aplica", ""
        }

        novas_features = {}
        for k, v in features.items():
            if v is None:
                continue

            str_v = str(v).strip()
            v_lower = str_v.lower()

            # Purga placeholders e valores não informados para evitar poluição visual e rejeição de anúncio
            if v_lower in PROIBIDOS or v_lower.startswith("preencher"):
                continue

            k_lower = str(k).lower()

            # 1. Inteiros para altura, largura, comprimento, profundidade
            if any(dim in k_lower for dim in ("altura", "largura", "comprimento", "profundidade")):
                def _arredondar_num(m):
                    num_str = m.group(0).replace(",", ".")
                    try:
                        return str(int(round(float(num_str))))
                    except ValueError:
                        return m.group(0)

                str_v = re.sub(r'\b\d+(?:[.,]\d+)?\b', _arredondar_num, str_v)

            # 2. Padronização de volume para 'mL' (capital L)
            if v_lower == "ml":
                str_v = "mL"
            else:
                str_v = re.sub(r'(\b\d+\s*)ml\b', r'\1mL', str_v, flags=re.IGNORECASE)
                str_v = re.sub(r'\bml\b', 'mL', str_v)
                str_v = re.sub(r'\bMl\b', 'mL', str_v)
            # Mapeia nomes alternativos de catálogo para nomes canônicos do ML
            k_canonico = k
            if k_lower == "volume da unidade":
                k_canonico = "Volume líquido"
            elif k_lower == "peso da unidade":
                k_canonico = "Peso líquido"

            # 3. Força 'Tipo de unidade' e 'Unidade de venda' como 'Unidade'
            if k_lower in ("tipo de unidade", "unidade de venda"):
                str_v = "Unidade"

            # Purga chaves desnecessárias de catálogo que não existem no formulário do BaseLinker
            if k_lower in ("funções", "formato do produto", "ambientes", "com secagem rápida"):
                continue

            novas_features[k_canonico] = str_v

        return novas_features

    def _chamar_llm_ficha_tecnica(self, contexto: str, atributos_obrigatorios: List[str]) -> Dict:
        """Chama o Groq/Llama para extrair atributos técnicos a partir do contexto local do produto."""
        import json

        input_tech = f"{contexto}\nCampos Obrigatórios: {', '.join(atributos_obrigatorios)}"
        res_tech = self.groq.chat(
            system_prompt=self.PROMPT_TECH,
            user_input=input_tech,
            modelo="llama-3.3-70b-versatile",
        )
        try:
            if res_tech:
                data_tech = json.loads(res_tech.strip())
                return data_tech.get("atributos", {})
            logger.warning("Ficha Técnica LLM: resposta vazia.")
        except Exception as e:
            res_snippet = res_tech[:200] if res_tech else "None"
            logger.warning(f"Falha no JSON da Ficha Técnica LLM: {e} | {res_snippet}")
        return {}

    def _enriquecer_via_web(self, contexto: str, campos_ausentes: List[str]) -> Dict:
        """Pesquisa o produto na web e usa LLM para extrair dados técnicos dos campos ainda ausentes."""
        import re

        # Extrai o nome do produto do contexto (primeira linha após "Produto:")
        nome_produto = ""
        for linha in contexto.splitlines():
            if linha.strip().lower().startswith("produto:"):
                nome_produto = linha.split(":", 1)[-1].strip()
                break
        if not nome_produto:
            return {}

        # Extrai a marca do contexto (linha "Atributos:" ou campo "Marca" dentro do dict)
        marca = ""
        for linha in contexto.splitlines():
            if "atributos:" in linha.lower() or "features:" in linha.lower():
                match = re.search(r"['\"]?[Mm]arca['\"]?\s*:\s*['\"]?([A-Za-z0-9\s\-\.]+)['\"]?", linha)
                if match:
                    candidato = match.group(1).strip().strip("',}")
                    if candidato and "informado" not in candidato.lower():
                        marca = candidato
                        break

        conteudo_web = self._buscar_conteudo_web(nome_produto, marca=marca)
        if not conteudo_web:
            logger.info("[Web Enrich] Nenhum conteúdo útil encontrado na web para '%s'.", nome_produto[:60])
            return {}

        return self._extrair_atribs_do_conteudo_web(conteudo_web, nome_produto, campos_ausentes)

    def _buscar_conteudo_web(self, nome_produto: str, marca: str = "") -> str:
        """Busca e agrega conteúdo técnico sobre o produto nos sites dos fabricantes/distribuidores via DuckDuckGo HTML."""
        import re
        from urllib.parse import quote_plus, unquote
        from curl_cffi import requests

        # Limpa o nome do produto: remove SKU inicial (ex: "23131006900 | ") e prefixos "Kit N"
        nome_limpo = re.sub(r'^[\d\s\|\-]+', '', nome_produto).strip()
        nome_limpo = re.sub(r'^Kit\s+\d+\s+', '', nome_limpo, flags=re.IGNORECASE).strip()
        nome_limpo = re.sub(r'/\s*\d+\s*[gGkK]', '', nome_limpo).strip()

        if not nome_limpo:
            nome_limpo = nome_produto

        termo_marca = f" {marca.strip()}" if marca and marca.lower() not in nome_limpo.lower() else ""
        query_str = f"{nome_limpo}{termo_marca} ficha técnica especificações"
        logger.info("[Web Enrich] Buscando na web: '%s'", query_str[:60])

        dominios_excluir = [
            "duckduckgo", "google", "youtube", "facebook", "instagram", "wikipedia",
            "mercadolivre", "amazon", "shopee", "americanas", "submarino",
            "magazineluiza", "magalu", "extra", "kabum", "buscape"
        ]

        conteudo_total = ""

        try:
            url_search = f"https://html.duckduckgo.com/html/?q={quote_plus(query_str)}"
            resp = requests.get(url_search, impersonate="chrome110", timeout=10)
            if resp.status_code == 200:
                # 1. Extrai trechos de texto dos resultados da busca (snippets)
                snippets = re.findall(r'<a class="result__snippet[^>]*>(.*?)</a>', resp.text, re.DOTALL)
                for s in snippets[:8]:
                    limpo = re.sub(r'<[^>]+>', ' ', s)
                    limpo = re.sub(r'\s+', ' ', limpo).strip()
                    if len(limpo) > 20:
                        conteudo_total += f"\n- {limpo}"

                # 2. Tenta acessar as 2 primeiras páginas técnicas encontradas
                raw_urls = re.findall(r'<a class="result__url"[^>]*href="([^"]+)"', resp.text)
                clean_urls = []
                for u in raw_urls:
                    decoded = unquote(u.strip())
                    if "//duckduckgo.com/l/?uddg=" in decoded:
                        decoded = unquote(decoded.split("uddg=")[-1].split("&")[0])
                    if not any(bloco in decoded.lower() for bloco in dominios_excluir):
                        clean_urls.append(decoded)

                for site_url in clean_urls[:2]:
                    try:
                        r = requests.get(site_url, impersonate="chrome110", timeout=8)
                        if r.status_code == 200 and len(r.text) > 500:
                            texto_limpo = re.sub(r'<[^>]+>', ' ', r.text)
                            texto_limpo = re.sub(r'\s+', ' ', texto_limpo).strip()
                            conteudo_total += f"\n\n[Fonte: {site_url[:80]}]\n{texto_limpo[:2500]}"
                            if len(conteudo_total) > 8000:
                                break
                    except Exception as e_site:
                        logger.debug("[Web Enrich] Falha ao acessar %s: %s", site_url[:80], str(e_site))
                        continue

            return conteudo_total.strip()

        except Exception as e:
            logger.warning("[Web Enrich] Erro na busca web para '%s': %s", nome_limpo[:50], str(e))
            return ""

    def _extrair_atribs_do_conteudo_web(self, conteudo_web: str, nome_produto: str, campos_ausentes: List[str]) -> Dict:
        """Usa o LLM para extrair atributos técnicos específicos do conteúdo de páginas web."""
        import json

        campos_str = ", ".join(campos_ausentes)
        prompt_web = (
            "Você é um extrator de dados técnicos de produtos. "
            "Analise o conteúdo de páginas web abaixo e extraia EXCLUSIVAMENTE valores "
            "que estejam EXPLICITAMENTE presentes no texto. "
            "NUNCA invente ou infira valores. Se um campo não estiver no texto, omita-o do JSON. "
            "Retorne SOMENTE JSON válido no formato: {\"atributos\": {\"Campo\": \"Valor\"}}"
        )
        user_input = (
            f"Produto: {nome_produto}\n"
            f"Campos a extrair: {campos_str}\n\n"
            f"Conteúdo das páginas:\n{conteudo_web[:6000]}"
        )

        try:
            res = self.groq.chat(
                system_prompt=prompt_web,
                user_input=user_input,
                modelo="llama-3.3-70b-versatile",
            )
            if res:
                data = json.loads(res.strip())
                atribs = data.get("atributos", {})
                encontrados = [k for k, v in atribs.items() if v and v != "Não informado"]
                logger.info("[Web Enrich] Campos preenchidos via web: %s", encontrados)
                return atribs
        except Exception as e:
            logger.warning("[Web Enrich] Falha ao extrair atributos do conteúdo web: %s", str(e))
        return {}

    def _gerar_keywords(self, titulo: str, contexto: str) -> Dict:
        """Gera palavras-chave SEO via Groq."""
        import json

        keywords_data = {}
        res_key = self.groq.chat(
            system_prompt=self.PROMPT_KEYWORDS,
            user_input=f"Título: {titulo}\n{contexto}",
            modelo="llama-3.1-8b-instant",
        )
        try:
            if res_key:
                keywords_data = json.loads(res_key.strip())
                total_keys = (
                    len(keywords_data.get('termos_primarios', []))
                    + len(keywords_data.get('termos_secundarios', []))
                )
                logger.info(f"Palavras-chave geradas: {total_keys} termos.")
            else:
                logger.warning("Palavras-chave: resposta vazia.")
        except Exception as e:
            res_snippet = res_key[:200] if res_key else "None"
            logger.warning(f"Falha no JSON de Palavras-chave: {e} | {res_snippet}")

        return keywords_data


# =============================================================================
# CRIADOR KITS SERVICE
# =============================================================================

class CriadorKitsService:

    ID_INVENTARIO     = 24542
    ID_CATEGORIA_KITS = 979589
    MAX_RETRIES       = 3
    DELAY_RETRY       = 1.0
    DELAY_RATE_LIMIT  = 3.0

    def __init__(self) -> None:
        self.client = BaselinkerClient()
        self.seo    = SEOGenerator()
        self._mapa_tags_cache: Optional[Dict[str, int]] = None
        self._mapa_fabricantes_cache: Optional[Dict[int, str]] = None

    def _selecionar_dimensoes_embalagem(self, dados: Dict, quantidade: int) -> Dict[str, int]:
        """
        Seleciona uma embalagem compatível para um kit com base nas dimensões da unidade.
        Para kits com mais de uma unidade, retorna as dimensões da embalagem que comporta
        a quantidade total. Para unidades simples, mantém as dimensões originais (arredondadas para inteiro).
        """
        try:
            altura = float(dados.get("height") or 0)
            largura = float(dados.get("width") or 0)
            comprimento = float(dados.get("length") or 0)
        except (TypeError, ValueError):
            return {"height": 0, "width": 0, "length": 0}

        def _arredondar(v: float) -> int:
            return int(round(float(v))) if v else 0

        if quantidade <= 1 or altura <= 0 or largura <= 0 or comprimento <= 0:
            return {
                "height": _arredondar(altura),
                "width":  _arredondar(largura),
                "length": _arredondar(comprimento),
            }

        dimensoes_unidade = sorted([altura, largura, comprimento], reverse=True)

        caixas = [
            (27, 22, 22),
            (21, 21, 14),
            (25, 30, 17),
            (21, 17, 14),
            (27, 21, 13),
            (27, 21, 17),
            (18, 34, 14),
        ]
        sacos = [
            (20, 30),
            (12, 18),
            (19, 25),
            (20, 40),
            (10, 30),
            (15, 25),
            (15, 40),
            (40, 60),
            (30, 40),
        ]

        def unidades_que_cabem(dims_unidade, dims_embalagem):
            if any(p <= 0 for p in dims_unidade):
                return 0
            if any(p > e for p, e in zip(dims_unidade, dims_embalagem)):
                return 0
            qtd_por_eixo = [int(e // p) for p, e in zip(dims_unidade, dims_embalagem)]
            return qtd_por_eixo[0] * qtd_por_eixo[1] * qtd_por_eixo[2]

        candidatos = []
        for caixa in caixas:
            dims_caixa = sorted(caixa, reverse=True)
            if unidades_que_cabem(dimensoes_unidade, dims_caixa) >= quantidade:
                volume = dims_caixa[0] * dims_caixa[1] * dims_caixa[2]
                candidatos.append((volume, caixa))

        if altura <= 5:
            dims_unidade_2d = sorted([largura, comprimento], reverse=True)
            for saco in sacos:
                dims_saco = sorted(saco, reverse=True)
                if any(p > e for p, e in zip(dims_unidade_2d, dims_saco)):
                    continue
                qtd_por_eixo = [int(e // p) for p, e in zip(dims_unidade_2d, dims_saco)]
                unidades_cabem = qtd_por_eixo[0] * qtd_por_eixo[1]
                if unidades_cabem >= quantidade:
                    volume = dims_saco[0] * dims_saco[1] * 5
                    candidatos.append((volume, (saco[0], saco[1], 5)))

        if not candidatos:
            return {
                "height": _arredondar(altura),
                "width":  _arredondar(largura),
                "length": _arredondar(comprimento),
            }

        _, melhor = min(candidatos, key=lambda item: item[0])
        return {
            "height": _arredondar(melhor[2]),
            "width":  _arredondar(melhor[0]),
            "length": _arredondar(melhor[1]),
        }

    def _get_mapa_tags(self) -> Dict[str, int]:
        if self._mapa_tags_cache is None:
            self._mapa_tags_cache = self.client.obter_mapa_tags()
            if not self._mapa_tags_cache:
                logger.error("Mapa de tags retornado VAZIO do BaseLinker.")
            else:
                logger.info(f"Mapa de tags carregado: {list(self._mapa_tags_cache.keys())}")
        return self._mapa_tags_cache

    # ── API BaseLinker ────────────────────────────────────────────────────────

    def _request_seguro(self, method: str, params: Dict) -> Dict:
        ultimo_erro = None
        for tentativa in range(1, self.MAX_RETRIES + 1):
            try:
                return self.client._request(method, params)
            except Exception as exc:
                ultimo_erro = exc
                if "429" in str(exc):
                    time.sleep(self.DELAY_RATE_LIMIT)
                else:
                    time.sleep(self.DELAY_RETRY)
        raise Exception(f"API Baselinker falhou em '{method}': {ultimo_erro}")

    def _baixar_dados_produto(self, pid: str) -> Optional[Dict]:
        """
        Baixa os dados do produto e os padroniza em um dicionário plano (flat),
        resolvendo campos de texto, NCM, Marca e Imagem.
        """
        try:
            r = self._request_seguro(
                "getInventoryProductsData",
                {"inventory_id": self.ID_INVENTARIO, "products": [int(pid)]},
            )
            produtos = r.get("products", {})
            raw = produtos.get(int(pid)) or produtos.get(str(pid))
            if not raw:
                return None

            sku = raw.get("sku", "")
            return self.client.buscar_produto_por_sku(sku)
        except Exception as exc:
            logger.error(f"Falha download/padronização PID={pid}: {exc}")
            return None

    def buscar_pid_por_sku(self, sku: str) -> Optional[str]:
        try:
            resp = self._request_seguro(
                "getInventoryProductsList",
                {"inventory_id": self.ID_INVENTARIO, "filter_sku": str(sku)},
            )
            produtos = resp.get("products", {})
            if not produtos:
                return None
            return str(next(iter(produtos.keys())))
        except Exception as exc:
            logger.error(f"buscar PID SKU={sku}: {exc}")
            return None

    # ── Preço ─────────────────────────────────────────────────────────────────

    def _calcular_preco(self, dados: Dict, sku_novo: str) -> float:
        try:
            resultado = Calculadora.calcular(dados, sku_ref=sku_novo)
            return resultado.preco_final
        except Exception as exc:
            logger.error(f"Calculadora falhou SKU={sku_novo}: {exc}")
            regras = RegrasPrecificacao.get_ativa()
            return float(regras.piso_valor_forcado)

    def _montar_prices_dict(self, preco: float) -> Dict[str, float]:
        return {"53516": preco}

    # ── Textos ────────────────────────────────────────────────────────────────

    def _extrair_features(self, dados: Dict) -> Dict:
        tf = dados.get("text_fields", {}) or {}
        if isinstance(tf, list):
            tf = {}
        feat_raw = tf.get("features") or dados.get("features")
        features: Dict = {}
        if isinstance(feat_raw, list):
            for item in feat_raw:
                if isinstance(item, dict):
                    features[item.get("name")] = item.get("value")
        elif isinstance(feat_raw, dict):
            features = dict(feat_raw)
        return self._padronizar_atributos_features(features)

    @staticmethod
    def _padronizar_atributos_features(features: Dict) -> Dict:
        return SEOGenerator._padronizar_atributos_features(features)

    @staticmethod
    def _resolver_atributos_fisicos_kit(features: Dict, quantidade: int = 1) -> Dict:
        """
        Garante a integridade fática de kits e unidades:
        - Se quantidade > 1: Formato de venda = 'Kit', Quantidade = N, Unidades por kit = N, Unidades por embalagem = N
        - Se quantidade == 1: Formato de venda = 'Unidade', Quantidade = 1, Unidades por kit = 1, Unidades por embalagem = 1
        - Condição do item = 'Novo'
        """
        if not isinstance(features, dict):
            return features

        novas_features = dict(features)
        qtd_str = str(quantidade)
        fmt_venda = "Kit" if quantidade > 1 else "Unidade"

        for k in list(novas_features.keys()):
            k_lower = str(k).strip().lower()

            if k_lower in ("formato de venda", "formato de venda ", "formatodevenda"):
                novas_features[k] = fmt_venda

            if k_lower in ("quantidade", "quantidade ", "quantidade de peças", "quantidade de unidades"):
                novas_features[k] = qtd_str

            if k_lower in ("unidades por kit", "unidades por embalagem"):
                novas_features[k] = qtd_str

            if k_lower in ("condição do item", "item_condition"):
                novas_features[k] = "Novo"

        novas_features["Formato de venda"] = fmt_venda
        novas_features["Unidades por kit"] = qtd_str
        novas_features["Quantidade"] = qtd_str
        novas_features["Condição do item"] = "Novo"
        novas_features["Tipo de unidade"] = "Unidade"
        novas_features["Unidade de venda"] = "Unidade"

        return novas_features

    def _extrair_imagens(self, dados: Dict) -> Dict[str, str]:
        imgs_raw = dados.get("images") or []
        urls: List[str] = []
        if isinstance(imgs_raw, dict):
            for v in imgs_raw.values():
                if isinstance(v, str):
                    urls.append(v.removeprefix("url:").strip())
        elif isinstance(imgs_raw, list):
            for item in imgs_raw:
                if isinstance(item, str):
                    urls.append(item.removeprefix("url:").strip())
                elif isinstance(item, dict):
                    u = item.get("url") or item.get("src") or ""
                    if u:
                        urls.append(u.removeprefix("url:").strip())
        return {str(i): f"url:{u}" for i, u in enumerate(u for u in urls if u.startswith("http"))}

    def _montar_campos_texto(
        self,
        dados: Dict,
        sku_novo: str,
        dados_secundario: Optional[Dict] = None,
        category_id_ml: Optional[str] = None,
    ) -> Dict:
        import re

        tf = dados.get("text_fields", {}) or {}
        if isinstance(tf, list):
            tf = {}
        features = self._extrair_features(dados)
        e_misto  = dados_secundario is not None

        lista_obrigatorios_nomes = self._buscar_atributos_obrigatorios_ml(category_id_ml, features, dados)

        nome_original      = str(tf.get("name") or dados.get("name") or "").strip()
        descricao_original = str(tf.get("description") or dados.get("description") or "").strip()

        imgs_map = self._extrair_imagens(dados)
        urls_imgs = [v.removeprefix("url:") for v in imgs_map.values() if v.startswith("url:http")]
        ean_val = str((features or {}).get("EAN") or (features or {}).get("ean") or (features or {}).get("Código universal de produto") or "")
        marca_val = str((features or {}).get("Marca") or (features or {}).get("marca") or "")

        conteudo = self.seo.gerar_conteudo(
            nome_original,
            descricao_original,
            features,
            e_misto=e_misto,
            atributos_obrigatorios=lista_obrigatorios_nomes,
            category_id_ml=category_id_ml,
            imagens_urls=urls_imgs,
            ean=ean_val,
            marca=marca_val,
        )

        atribs_ia = conteudo.get("atributos") or {}

        def limpar_nome(nome: str) -> str:
            return re.sub(r'\s*\(Valores permitidos:.*?\)', '', nome).strip()

        for k, v in atribs_ia.items():
            k_limpo = limpar_nome(k)
            if v and v != "null":
                features[k_limpo] = v

        for n_prompt in lista_obrigatorios_nomes:
            n_limpo = limpar_nome(n_prompt)
            if n_limpo not in features:
                if "(Valores permitidos:" in n_prompt:
                    dica = n_prompt.split("(Valores permitidos:")[1].strip(" )")
                    features[n_limpo] = f"PREENCHER (Opções: {dica})"
                else:
                    features[n_limpo] = "PREENCHER"

        desc_ajustada = ajustar_descricao_quantidade(
            conteudo["descricao"],
            quantidade=1,
            nome_produto=nome_original,
            nomes_secundarios=[str((dados_secundario.get("text_fields", {}) or {}).get("name") or dados_secundario.get("name") or "").strip()] if dados_secundario else None,
        )

        campos: Dict = {
            "name":        conteudo["titulo"],
            "description": desc_ajustada,
            "features":    features,
        }
        for chave, valor in tf.items():
            if isinstance(chave, str) and chave.startswith("extra_field_"):
                campos[chave] = valor
        campos["extra_field_39310"] = "un"
        
        # Força os campos obrigatórios na Ficha Técnica (Features)
        if "features" in campos:
            campos["features"] = self._resolver_atributos_fisicos_kit(campos["features"], quantidade=1)

            # Limpa Modelo se for puramente numérico (SKU) ou usar placeholders proibidos
            for k in list(campos["features"].keys()):
                if "modelo" in k.lower() or "model" in k.lower():
                    v = str(campos["features"][k]).strip()
                    if not v or v.isdigit() or any(forbidden in v.lower() for forbidden in ["não informado", "a informar", "desconhecido", "n/a", "null", "preencher", "não se aplica", "nao se aplica"]):
                        del campos["features"][k]

        campos["features"] = self._padronizar_atributos_features(campos.get("features", {}))
        return campos

    def _buscar_atributos_obrigatorios_ml(
        self, category_id_ml: str, features: Dict, dados: Dict
    ) -> List[str]:
        lista_obrigatorios_nomes = []
        termos_ignorar = [
            "motivo de gtin vazio", "reason_for_no_gtin", "motivo de gtin isento",
            "código universal", "ean", "gtin", "código de barras",
            "autorização sanitária", "aviso de operação", "certificado da afe",
            "licença do conama", "r.s/nso", "registro de producto do ms",
            "cofepris", "rpptm", "registro/nso", "comunicação prévia na anvisa",
            "iva", "ieps", "imposto", "chave do produto", "chave da unidade", "nota fiscal"
        ]
        
        if category_id_ml and category_id_ml != "979589":
            try:
                logger.warning(f"[Ficha Técnica] Verificando campos da Ficha Técnica para Cat: {category_id_ml}")
                ml_client = MercadoLivreClient()
                campos_raw = ml_client.ficha_tecnica(category_id_ml, apenas_obrigatorios=False)
                nomes_existentes = {k.lower() for k in features.keys()}

                for f in campos_raw:
                    n = f.get("name")
                    if not n: continue
                    
                    # Filtra termos de identificação que o sistema já resolve ou que o usuário pediu para tirar
                    if any(t in n.lower() for t in termos_ignorar):
                        continue

                    allowed_values = f.get("values", [])
                    if n.lower() not in nomes_existentes:
                        valor_fisico = self._resolver_campo_fisico(n, dados)
                        if valor_fisico:
                            features[n] = valor_fisico
                        else:
                            tag_status = "[OBRIGATÓRIO]" if f.get("required") else "[RECOMENDADO]"
                            campo_prompt = f"{n} {tag_status}"
                            if allowed_values:
                                vals_str = ", ".join(allowed_values[:10])
                                campo_prompt = f"{n} {tag_status} (Valores permitidos: {vals_str})"
                            lista_obrigatorios_nomes.append(campo_prompt)

                logger.info(f"[Ficha Técnica] Campos estruturados para IA: {len(lista_obrigatorios_nomes)}")
            except Exception as exc:
                logger.error(f"[Ficha Técnica] Erro ao buscar dados do ML: {exc}")
        return lista_obrigatorios_nomes

    # ── Payload ─────────────────────────────────────────────────────────────────

    def _montar_payload(
        self,
        dados: Dict,
        sku_novo: str,
        pid: str,
        dados_secundario: Optional[Dict] = None,
        pid_secundario: Optional[str] = None,
        category_id_bl: Optional[int] = None,
        category_id_ml: Optional[str] = None,
    ) -> Dict:
        if dados_secundario:
            custo1 = self._extrair_custo(dados)
            custo2 = self._extrair_custo(dados_secundario)
            peso1  = float(dados.get("weight") or 0)
            peso2  = float(dados_secundario.get("weight") or 0)
            dados_calc = {"average_cost": custo1 + custo2, "weight": peso1 + peso2}
            preco = self._calcular_preco(dados_calc, sku_novo)
        else:
            preco = self._calcular_preco(dados, sku_novo)

        campos  = self._montar_campos_texto(dados, sku_novo, dados_secundario, category_id_ml)
        imagens = self._extrair_imagens(dados)

        bundle = {str(pid): 1}
        if pid_secundario:
            bundle[str(pid_secundario)] = 1

        mapa_tags = self._get_mapa_tags()
        tags_enviar = []
        modalidade_tag = "premium" if dados_secundario else "classico"
        tag_val = mapa_tags.get(modalidade_tag)
        if tag_val:
            tags_enviar.append(tag_val)
        else:
            logger.warning(f"Tag '{modalidade_tag}' não encontrada no BaseLinker.")

        return {
            "inventory_id":    self.ID_INVENTARIO,
            "sku":             sku_novo,
            "ean":             str(dados.get("ean", "") or ""),
            "manufacturer_id": dados.get("manufacturer_id") or 0,
            "text_fields":     campos,
            "category_id":     category_id_bl or self.ID_CATEGORIA_KITS,
            "price_brutto":    preco,
            "prices":          self._montar_prices_dict(preco),
            "weight":          (float(dados.get("weight") or 0) + float((dados_secundario or {}).get("weight") or 0)),
            "height":          int(round(float(dados.get("height", 0) or 0))),
            "width":           int(round(float(dados.get("width", 0) or 0))),
            "length":          int(round(float(dados.get("length", 0) or 0))),
            "tags":            tags_enviar,
            "images":          imagens,
            "is_bundle":       1,
            "bundle_products": bundle,
        }

    @staticmethod
    def _extrair_custo(dados: Dict) -> float:
        for campo in ("average_cost", "price_brutto", "price"):
            v = dados.get(campo)
            if v:
                try:
                    return float(str(v).replace(",", "."))
                except (ValueError, TypeError):
                    pass
        for chave, valor in dados.items():
            if isinstance(chave, str) and chave.startswith("prices_"):
                try:
                    f = float(str(valor).replace(",", "."))
                    if f > 0:
                        return f
                except (ValueError, TypeError):
                    pass
        return 0.0

    def _obter_fallback_gpc_por_ncm(self, ncm: str) -> Optional[str]:
        """
        Retorna um GPC seguro e ativo baseado nos primeiros dígitos do NCM do produto.
        Caso não mapeie, retorna None.
        """
        ncm_limpo = str(ncm).replace(".", "").strip()
        
        # Torneiras, registros, válvulas (NCM 8481...)
        if ncm_limpo.startswith("8481"):
            return "10002602"  # Torneiras / Misturadores
            
        # Cabos elétricos, fios, conectores (NCM 8544...)
        if ncm_limpo.startswith("8544"):
            return "10000586"  # Cabos / Conectores de Energia
            
        # Adesivos, colas, silicones, fitas autoadesivas (NCM 3506..., 3919...)
        if ncm_limpo.startswith(("3506", "3919", "3214")):
            return "10000624"  # Adesivos / Colas
            
        return None

    def _obter_ean_kit(
        self,
        sku_origem: str,
        quantidade: int,
        dados_completos: Dict,
        sku_kit_final: str,
        titulo_final: str,
        forcar_unitario: bool = False,
        gpc_manual: Optional[str] = None,
        forcar_novo_gtin: bool = False,
    ) -> str:
        """
        Obtém ou gera um EAN para o kit.
        Unitário (qtd=1) -> sem novo EAN, exceto se forcar_unitario for True.
        Kit (qtd>1) -> Novo EAN registrado na GS1 Brasil.
        forcar_novo_gtin=True -> não reaproveita GTIN de criações anteriores do mesmo SKU.
        """
        if quantidade <= 1 and not forcar_unitario:
            return ""

        # Busca inteligente por quantidade no banco de dados local para compartilhamento de EAN (apenas se não forçar novos GTINs)
        if not forcar_novo_gtin:
            gtin_existente = GtinKit.objects.filter(
                sku_origem=sku_origem,
                quantidade=quantidade
            ).exclude(gtin='PENDENTE').exclude(gtin='').first()

            if gtin_existente:
                logger.info(f"Reaproveitando EAN {gtin_existente.gtin} da variação {gtin_existente.sku_kit} para a quantidade {quantidade}")
                obj, created = GtinKit.objects.get_or_create(
                    sku_kit=sku_kit_final,
                    defaults={
                        'sku_origem': sku_origem,
                        'quantidade': quantidade,
                        'gtin': gtin_existente.gtin,
                    },
                )
                if not created and obj.gtin != gtin_existente.gtin:
                    obj.gtin = gtin_existente.gtin
                    obj.save()
                return gtin_existente.gtin

        obj, created = GtinKit.objects.get_or_create(
            sku_kit=sku_kit_final,
            defaults={
                'sku_origem': sku_origem,
                'quantidade': quantidade,
                'gtin': 'PENDENTE',
            },
        )

        if not created and obj.gtin and obj.gtin != 'PENDENTE':
            logger.info(f"Reaproveitando EAN {obj.gtin} para SKU Kit {sku_kit_final}")
            return obj.gtin

        try:
            gpc = None
            ncm_bruto = str(dados_completos.get("ncm", "00000000"))
            ncm_limpo = ncm_bruto.replace(".", "").strip()

            # 1. Se foi enviado GPC manual e for válido (8 dígitos numéricos)
            if gpc_manual:
                gpc_manual_limpo = str(gpc_manual).strip()
                if gpc_manual_limpo.isdigit() and len(gpc_manual_limpo) == 8:
                    gpc = gpc_manual_limpo
                    logger.info(f"GPC manual fornecido pelo painel: {gpc}")
                else:
                    logger.warning(f"GPC manual fornecido '{gpc_manual}' é inválido (deve ter 8 dígitos numéricos).")

            # 2. Se não foi fornecido GPC manual ou se ele é inválido, tenta obter do fallback estático de NCM
            if not gpc and ncm_limpo:
                logger.info("Buscando GPC no fallback estático por NCM...")
                gpc_fallback = self._obter_fallback_gpc_por_ncm(ncm_limpo)
                if gpc_fallback:
                    gpc = gpc_fallback
                    logger.info(f"GPC determinado via fallback estático de NCM: {gpc}")

            if not gpc:
                raise Exception("GPC não pôde ser determinado (nenhum GPC manual válido fornecido e nenhum fallback aplicável encontrado).")

            marca_real = dados_completos.get("marca")
            m_id = dados_completos.get("manufacturer_id")
            if m_id:
                mapa_marcas = self._get_mapa_fabricantes()
                marca_real = mapa_marcas.get(int(m_id)) or marca_real

            if not marca_real or "não informada" in marca_real.lower():
                marca_real = "Sanol"

            dados_gs1 = dados_completos.copy()
            dados_gs1["sku"]   = sku_kit_final
            dados_gs1["nome"]  = titulo_final
            dados_gs1["marca"] = marca_real

            ncm_bruto = str(dados_completos.get("ncm", "00000000"))
            dados_gs1["ncm"] = ncm_bruto.replace(".", "").strip()

            gs1_client = get_gs1_client()
            res = gs1_client.gerar_ean_completo(dados_gs1, gpc)

            if res.get('sucesso'):
                novo_gtin = res['dados']['gtin']
                obj.gtin = novo_gtin
                obj.save()
                logger.info(f"EAN oficial gerado na GS1: {novo_gtin}")
                return novo_gtin
            else:
                raise Exception(f"Falha no registro GS1: {res.get('erro')}")

        except Exception as e:
            import traceback
            logger.error(f"Falha crítica no processo GS1: {e}")
            
            # Se for unitário (Qtd = 1), tenta usar o EAN original do produto base se for válido
            if quantidade == 1:
                ean_original = str(dados_completos.get("ean") or "").strip()
                # Verifica se é um EAN brasileiro legítimo ou qualquer EAN válido (mínimo de 12 dígitos)
                if ean_original and len(ean_original) >= 12 and ean_original.lower() != "pendente":
                    logger.warning(f"GS1 falhou para unitário. Utilizando EAN original como fallback: {ean_original}")
                    obj.gtin = ean_original
                    obj.save()
                    return ean_original

            # Se não puder usar o original ou se for kit (Qtd > 1):
            # Para evitar rejeições duras nos marketplaces por EAN/GTIN inválido (ex: iniciado com '1'),
            # salvamos como 'PENDENTE' no banco local e retornamos vazio ("") para que o produto seja publicado sem EAN,
            # permitindo que o anúncio vá ao ar e o usuário possa corrigir o EAN posteriormente.
            logger.warning(f"GS1 falhou para kit/unitário (Qtd={quantidade}). Retornando vazio para evitar GTIN inválido.")
            obj.gtin = "PENDENTE"
            obj.save()
            return ""

    def _get_mapa_fabricantes(self) -> Dict[int, str]:
        if self._mapa_fabricantes_cache is None:
            try:
                resp = self.client._request("getInventoryManufacturers", {"inventory_id": self.ID_INVENTARIO})
                mans = resp.get("manufacturers", [])
                self._mapa_fabricantes_cache = {
                    int(m["manufacturer_id"]): m["name"] for m in mans if "name" in m
                }
                logger.info(f"Mapa de {len(self._mapa_fabricantes_cache)} fabricantes carregado.")
            except Exception as exc:
                logger.error(f"Erro ao carregar fabricantes: {exc}")
                self._mapa_fabricantes_cache = {}
        return self._mapa_fabricantes_cache

    def _resolver_campo_fisico(self, nome_campo: str, dados: Dict, quantidade: int = 1) -> Optional[str]:
        n = nome_campo.lower()

        if "modelo" in n or "model" in n:
            return None

        if "marca" == n or "fabricante" == n:
            m_id = dados.get("manufacturer_id")
            if m_id:
                mapa = self._get_mapa_fabricantes()
                return mapa.get(int(m_id))
            return None

        if "ean" in n or "código universal" in n:
            val = dados.get("ean")
            return str(val).strip() if val else None

        if is_unidades_por_kit_attribute(nome_campo):
            return str(quantidade)

        if "altura" in n:
            val = dados.get("height")
            return f"{int(round(float(val)))} cm" if val and float(val) > 0 else None

        if "largura" in n:
            val = dados.get("width")
            return f"{int(round(float(val)))} cm" if val and float(val) > 0 else None

        if "comprimento" in n or "profundidade" in n:
            val = dados.get("length")
            return f"{int(round(float(val)))} cm" if val and float(val) > 0 else None

        if "peso" in n:
            val = dados.get("weight")
            if val and float(val) > 0:
                return f"{val} kg" if float(val) >= 1 else f"{float(val) * 1000:.0f} g"
            return None

        return None

    # ── Execução ──────────────────────────────────────────────────────────────

    def _validar_conteudo_ia(self, conteudo: Dict, nome_original: str) -> Optional[str]:
        """
        Valida se a IA gerou título e descrição válidos.
        Retorna uma mensagem de erro se a geração falhou, ou None se estiver OK.
        Critérios:
        - Título: não vazio, comprimento mínimo de 10 chars e não igual ao nome original bruto.
        - Descrição: não vazia, comprimento mínimo de 100 chars e contém ao menos uma seção obrigatória.
        """
        erros = []

        titulo = (conteudo.get("titulo") or "").strip()
        if not titulo or len(titulo) < 10:
            erros.append(f"título inválido ou ausente (gerado: {titulo!r})")

        desc = (conteudo.get("descricao") or "").strip()
        desc_norm = normalizar_texto_validador(desc)
        secoes_minimas_norm = ["sobre o produto", "beneficios"]
        if not desc or len(desc) < 100:
            erros.append(f"descrição inválida ou ausente (tamanho: {len(desc)} chars)")
        elif not any(s in desc_norm for s in secoes_minimas_norm):
            # Se possuir um tamanho mínimo bom (>150 chars), prosseguimos com warning no log em vez de abortar duramente
            if len(desc) < 150:
                erros.append("descrição não contém as seções obrigatórias (SOBRE O PRODUTO, BENEFÍCIOS)")
            else:
                logger.warning("Descrição não contém as seções mínimas exigidas de forma clara, mas possui tamanho aceitável. Prosseguindo para evitar falha fatal no kit.")

        if erros:
            msg = f"Falha na geração de conteúdo pela IA: {'; '.join(erros)}. Criação abortada para não gerar anúncio sem texto."
            logger.error(msg)
            return msg
        return None

    def _validar_dados_essenciais(self, dados: Dict) -> Optional[str]:
        """
        Verifica se o produto possui imagem, peso e dimensões maiores que zero.
        Retorna uma mensagem de erro se faltar algo, ou None se estiver OK.
        """
        erros = []
        sku = dados.get("sku", "N/A")
        
        if not dados.get("imagem_url"):
            erros.append("imagem")
        
        if float(dados.get("weight", 0)) <= 0:
            erros.append("peso")
            
        if float(dados.get("height", 0)) <= 0:
            erros.append("altura")
            
        if float(dados.get("width", 0)) <= 0:
            erros.append("largura")
            
        if erros:
            msg = f"Produto base (SKU {sku}) bloqueado por falta de: {', '.join(erros)}."
            logger.error(msg)
            return msg
        return None

    def executar_criacao_grade(
        self,
        pid: str,
        sku_origem: str,
        sku_secundario: Optional[str] = None,
        category_id_ml: Optional[str] = None,
        gerar_ean_unitario: bool = False,
        gpc_manual: Optional[str] = None,
        skus_secundarios: Optional[List[str]] = None,
        titulos_forcados: Optional[Dict[str, str]] = None,
        forcar_novos_gtins: bool = False,
    ) -> Dict:
        """
        Cria a grade completa de 8 anúncios (1/2/3/4un x Clássico/Premium) no BaseLinker.
        Permite vários SKUs secundários para Kit Misto.
        forcar_novos_gtins=True -> gera GTINs novos na GS1 para todos os kits, sem reaproveitar de lotes anteriores.
        """
        logger.warning(f"Iniciando criação de GRADE (8 anúncios) -> SKU Origem={sku_origem} | EAN Unitário={gerar_ean_unitario} | Novos GTINs={forcar_novos_gtins}")
        
        # Resolução de categoria (Exige categoria informada manualmente, sem adivinhação automática por título)
        if not category_id_ml or category_id_ml == "979589":
            return {"sucesso": False, "msg": "Categoria do Mercado Livre não informada. Por favor, forneça a árvore de categoria no formulário do painel."}
            
        # Inicializa o cache local de execução para compartilhar EANs entre Clássico e Premium
        self.gtins_gerados_lote: Dict[int, str] = {}

        # Registra o início de execução do lote no cache para controle de cancelamento em tempo real
        from django.core.cache import cache
        cache.set(f"criacao_lote_{sku_origem}", "executando", timeout=120)

        dados = self._baixar_dados_produto(pid)
        if not dados:
            return {"sucesso": False, "msg": "Falha ao baixar dados do produto principal."}

        # Trava de Segurança: Validação de dados essenciais
        erro_validacao = self._validar_dados_essenciais(dados)
        if erro_validacao:
            return {"sucesso": False, "msg": erro_validacao}

        if sku_origem == "30480":
            logger.warning(f"AUDITORIA PREÇO SKU 30480: Preço Base={dados.get('price_brutto')} | Prices={dados.get('prices')}")

        # Consolida a lista de SKUs secundários
        lista_skus_sec = []
        if skus_secundarios:
            for s in skus_secundarios:
                if s and s not in lista_skus_sec:
                    lista_skus_sec.append(s)
        if sku_secundario and sku_secundario not in lista_skus_sec:
            lista_skus_sec.append(sku_secundario)

        dados_secundarios = []
        pids_secundarios   = []
        for s_sec in lista_skus_sec:
            pid_sec = self.buscar_pid_por_sku(s_sec)
            if not pid_sec:
                return {"sucesso": False, "msg": f"SKU secundário {s_sec} não encontrado no BaseLinker."}
            d_sec = self._baixar_dados_produto(pid_sec)
            if not d_sec:
                return {"sucesso": False, "msg": f"Falha ao baixar dados do SKU secundário {s_sec}."}
            erro_sec = self._validar_dados_essenciais(d_sec)
            if erro_sec:
                return {"sucesso": False, "msg": f"Secundário ({s_sec}): {erro_sec}"}
            pids_secundarios.append(pid_sec)
            dados_secundarios.append(d_sec)

        # Para retrocompatibilidade com referências a dados_secundario/pid_secundario
        dados_secundario = dados_secundarios[0] if dados_secundarios else None
        pid_secundario   = pids_secundarios[0] if pids_secundarios else None

        # Resolução de categoria
        if not category_id_ml or category_id_ml == "979589":
            nome_base = (dados.get("text_fields", {}) or {}).get("name") or dados.get("name") or ""
            pred = MercadoLivreClient().predict_categoria(nome_base, limite=1)
            if pred:
                category_id_ml = str(pred[0]["category_id"])
                logger.info(f"Categoria prevista para grade: {category_id_ml}")

        category_id_bl = self.sincronizar_categoria_ml_para_baselinker(category_id_ml)

        # Cálculo da grade de preços
        try:
            from precificacao.services.calculadora import Calculadora

            vendas = self._get_vendas_60_dias(sku_origem)
            logger.info(f"Vendas 60 dias para {sku_origem}: {vendas}")

            if dados_secundario:
                custo1 = self._extrair_custo(dados)
                custo2 = self._extrair_custo(dados_secundario)
                peso1  = float(dados.get("weight") or 0)
                peso2  = float(dados_secundario.get("weight") or 0)
                dados_calc = {"average_cost": custo1 + custo2, "weight": peso1 + peso2}
                grade = Calculadora.calcular_grade(dados_calc, sku_ref=sku_origem, category_id=category_id_ml, vendas_60_dias=vendas)
            else:
                grade = Calculadora.calcular_grade(dados, sku_ref=sku_origem, category_id=category_id_ml, vendas_60_dias=vendas)
        except Exception as exc:
            return {"sucesso": False, "msg": f"Erro no motor de precificação: {exc}"}

        if grade.bloqueado:
            return {"sucesso": False, "msg": f"Grade bloqueada: {grade.motivo_bloqueio}"}

        # Coleta atributos e gera conteúdo base (uma única chamada à IA)
        features_base     = self._extrair_features(dados)
        lista_obrigatorios = self._buscar_atributos_obrigatorios_ml(category_id_ml, features_base, dados)
        imgs_map_grade = self._extrair_imagens(dados)
        urls_imgs_grade = [v.removeprefix("url:") for v in imgs_map_grade.values() if v.startswith("url:http")]
        ean_val_grade = str((features_base or {}).get("EAN") or (features_base or {}).get("ean") or (features_base or {}).get("Código universal de produto") or "")
        marca_val_grade = str((features_base or {}).get("Marca") or (features_base or {}).get("marca") or "")

        conteudo_base = self.seo.gerar_conteudo(
            dados.get("name", ""),
            dados.get("description", ""),
            features_base,
            e_misto=bool(dados_secundario),
            atributos_obrigatorios=lista_obrigatorios,
            gera_grade=True,
            category_id_ml=category_id_ml,
            imagens_urls=urls_imgs_grade,
            ean=ean_val_grade,
            marca=marca_val_grade,
        )

        titulos_grade = conteudo_base.get("titulos_grade", {})
        desc_base     = conteudo_base.get("descricao", "")
        atribs_ia     = conteudo_base.get("atributos", {})
        features_base.update(atribs_ia)

        # ── OVERRIDE TEMPORÁRIO: títulos forçados manualmente ────────────────
        # Quando titulos_forcados é passado (via painel_titulo_manual), substitui
        # os títulos gerados pela IA. Não afeta nenhum outro fluxo.
        if titulos_forcados:
            titulos_grade = titulos_forcados
            logger.info(f"[TITULO_MANUAL] Títulos da IA substituídos por títulos forçados para SKU {sku_origem}")

        # Trava de Segurança: Valida se a IA gerou título e descrição válidos
        nome_original_prod = (dados.get("text_fields", {}) or {}).get("name") or dados.get("name") or ""
        titulo_referencia = conteudo_base.get("titulo") or titulos_grade.get("1_classico") or ""
        conteudo_para_validar = {"titulo": titulo_referencia, "descricao": desc_base}
        erro_conteudo = self._validar_conteudo_ia(conteudo_para_validar, nome_original_prod)
        if erro_conteudo:
            return {"sucesso": False, "msg": erro_conteudo}


        campos_base = {
            "description": desc_base,
            "features":    features_base,
            "extra_field_39310": "un",
        }
        text_fields_originais = dados.get("text_fields", {})
        for k, v in text_fields_originais.items():
            if str(k).startswith("extra_field_") and k not in campos_base:
                campos_base[k] = v

        # Identifica se o produto base tem um EAN legítimo cadastrado no BaseLinker
        ean_original = str(dados.get("ean") or "").strip()
        ean_base_valido = bool(ean_original and ean_original.lower() != "pendente")
        precisa_gerar_unitario = gerar_ean_unitario or not ean_base_valido

        skus_criados = []
        ids_criados  = []

        for anuncio in grade.anuncios:
            if anuncio.bloqueado:
                logger.warning(f"Ignorando {anuncio.referencia}: Bloqueado.")
                continue

            # Verifica se o lote foi voluntariamente cancelado pelo usuário
            from django.core.cache import cache
            status_lote = cache.get(f"criacao_lote_{sku_origem}")
            if status_lote == "cancelado":
                logger.warning(f"Lote do SKU {sku_origem} foi voluntariamente CANCELADO pelo usuário durante o processamento.")
                # Executa o rollback completo de tudo que já foi criado até agora neste lote, inclusive na GS1
                self.executar_rollback_grade(sku_origem)
                return {
                    "sucesso": False,
                    "msg": f"Criação interrompida com sucesso. Todos os anúncios criados até o momento para o SKU {sku_origem} foram removidos e os GTINs correspondentes foram inativados/excluídos na GS1."
                }

            sku_novo = self._obter_proximo_sku(None)
            if not sku_novo:
                break

            # Seleciona título da grade para a variação
            chave_grade  = f"{anuncio.quantidade}_{anuncio.modalidade}"
            titulo_base  = titulos_grade.get(chave_grade) or titulos_grade.get(str(anuncio.quantidade))

            if not titulo_base:
                titulo_base = conteudo_base.get('titulo', '')

            if titulos_forcados:
                # Títulos forçados chegam prontos (ex: "Kit 2 Antena TV...") —
                # pula limpeza e re-prefixação que causariam duplicação do número.
                titulo_final = " ".join(titulo_base.split()).strip()
            else:
                # Limpeza agressiva: Remove Kit, Unidade, Unitário etc. do título base da IA
                termos_limpar = [
                    "Kit", "Unidade", "Unidades", "Unitário", "Unitario",
                    "Premium", "Clássico", "Classico", "Original", "Pack"
                ]
                for termo in termos_limpar:
                    # Regex para remover a palavra inteira isolada
                    titulo_base = re.sub(rf'\b{termo}\b', '', titulo_base, flags=re.IGNORECASE)

                titulo_final = " ".join(titulo_base.split()).strip()

                # Garante prefixo "Kit N" para kits ou limpa para unitário
                if anuncio.quantidade > 1:
                    titulo_final = f"Kit {anuncio.quantidade} {titulo_final}".strip()

                titulo_final = " ".join(titulo_final.split()).strip()

            # Ajuste final de tamanho por Python (Padding ou Corte)
            titulo_final = ajustar_titulo(titulo_final)
            titulo_final = titulo_final.strip(" -.,")

            # Tags
            mapa_tags   = self._get_mapa_tags()
            tags_enviar = []
            tag_name    = "premium" if anuncio.modalidade == "premium" else "classico"
            tag_val     = mapa_tags.get(tag_name)
            if tag_val:
                tags_enviar.append(tag_val)
            else:
                logger.warning(f"Tag '{tag_name}' não encontrada no BaseLinker.")

            # Se for um anúncio unitário clássico (Qtd = 1 e modalidade = classico), adiciona também a tag "Marketplace"
            if anuncio.quantidade == 1 and anuncio.modalidade == "classico":
                tag_marketplace = mapa_tags.get("marketplace")
                if tag_marketplace:
                    tags_enviar.append(tag_marketplace)
                else:
                    logger.warning("Tag 'Marketplace' não encontrada no BaseLinker.")

            ean_final = ""
            if anuncio.quantidade == 1 and not precisa_gerar_unitario:
                ean_final = dados.get("ean", "")

            # Atualiza e valida features por variação de kit
            features_var = features_base.copy()
            if ean_final:
                for k in list(features_var.keys()):
                    if "código universal" in k.lower() or "ean" in k.lower():
                        features_var[k] = ean_final

            features_var = self._resolver_atributos_fisicos_kit(features_var, quantidade=anuncio.quantidade)

            # Garante que o Modelo nunca seja inválido, vazio ou use placeholders proibidos
            has_modelo = False
            base_sku = str(dados.get("sku") or sku_origem).strip()
            for k in list(features_var.keys()):
                if "modelo" in k.lower() or "model" in k.lower():
                    has_modelo = True
                    v = str(features_var[k]).strip()
                    if not v or any(forbidden in v.lower() for forbidden in ["não informado", "a informar", "desconhecido", "n/a", "null", "preencher", "não se aplica", "nao se aplica"]):
                        features_var[k] = base_sku

            if not has_modelo:
                for n_p in lista_obrigatorios:
                    n_limpo = re.sub(r'\s*\(Valores permitidos:.*?\)', '', n_p).strip()
                    n_limpo = n_limpo.replace(" [OBRIGATÓRIO]", "").strip()
                    if "modelo" in n_limpo.lower() or "model" in n_limpo.lower():
                        features_var[n_limpo] = base_sku

            if not has_modelo:
                for n_p in lista_obrigatorios:
                    n_limpo = re.sub(r'\s*\(Valores permitidos:.*?\)', '', n_p).strip()
                    n_limpo = n_limpo.replace(" [OBRIGATÓRIO]", "").strip()
                    if "modelo" in n_limpo.lower() or "model" in n_limpo.lower():
                        features_var[n_limpo] = base_sku

            dimensoes_embalagem = self._selecionar_dimensoes_embalagem(dados, anuncio.quantidade)

            desc_anuncio = ajustar_descricao_quantidade(
                desc_base,
                quantidade=anuncio.quantidade,
                nome_produto=nome_original_prod,
                nomes_secundarios=[(d.get("text_fields", {}) or {}).get("name") or d.get("name") or "" for d in dados_secundarios],
            )

            payload = {
                "inventory_id":    self.ID_INVENTARIO,
                "sku":             sku_novo,
                "ean":             ean_final,
                "manufacturer_id": dados.get("manufacturer_id") or 0,
                "text_fields":     {
                    **campos_base,
                    "name":        titulo_final,
                    "features":    features_var,
                    "description": desc_anuncio,
                },
                "category_id":     category_id_bl,
                "price_brutto":    anuncio.preco_vitrine,
                "prices":          {"53516": anuncio.preco_vitrine},
                "weight":          (float(dados.get("weight") or 0) + sum(float(d.get("weight") or 0) for d in dados_secundarios)) * anuncio.quantidade,
                "height":          int(round(float(dimensoes_embalagem.get("height", 0) or 0))),
                "width":           int(round(float(dimensoes_embalagem.get("width", 0) or 0))),
                "length":          int(round(float(dimensoes_embalagem.get("length", 0) or 0))),
                "tags":            tags_enviar,
                "images":          self._extrair_imagens(dados),
                "is_bundle":       1,
                "bundle_products": {str(pid): anuncio.quantidade, **{str(p): anuncio.quantidade for p in pids_secundarios}},
            }

            try:
                id_kit = self._enviar_para_baselinker(payload)
                if not id_kit:
                    raise Exception("Falha ao obter ID do produto criado no BaseLinker.")

                self._salvar_historico(sku_origem, pid, sku_novo, id_kit)
                skus_criados.append(sku_novo)
                ids_criados.append(id_kit)

                if anuncio.quantidade > 1 or (anuncio.quantidade == 1 and precisa_gerar_unitario):
                    time.sleep(2)
                    # Verifica se o EAN para esta quantidade já foi gerado na execução deste lote/grade
                    if anuncio.quantidade in self.gtins_gerados_lote:
                        ean_final = self.gtins_gerados_lote[anuncio.quantidade]
                        logger.info(f"Herdando EAN {ean_final} do cache de lote para SKU Kit {sku_novo} (Qtd {anuncio.quantidade})")
                        # Cria o registro individual da variação no banco local apontando para o EAN herdado
                        GtinKit.objects.get_or_create(
                            sku_kit=sku_novo,
                            defaults={
                                'sku_origem': sku_origem,
                                'quantidade': anuncio.quantidade,
                                'gtin': ean_final,
                            }
                        )
                    else:
                        ean_final = self._obter_ean_kit(
                            sku_origem, anuncio.quantidade, dados, sku_novo, 
                            titulo_final, forcar_unitario=(anuncio.quantidade == 1 and precisa_gerar_unitario),
                            gpc_manual=gpc_manual,
                            forcar_novo_gtin=forcar_novos_gtins,
                        )
                        if ean_final and ean_final != 'PENDENTE':
                            self.gtins_gerados_lote[anuncio.quantidade] = ean_final

                if ean_final and (anuncio.quantidade > 1 or precisa_gerar_unitario):
                    for k in list(features_var.keys()):
                        if "código universal" in k.lower() or "ean" in k.lower():
                            features_var[k] = ean_final

                    update_payload = {
                        "inventory_id": self.ID_INVENTARIO,
                        "product_id":   int(id_kit),
                        "ean":          ean_final,
                        "text_fields":  {
                            **payload["text_fields"],
                            "features":    features_var,
                            "description": desc_anuncio,
                        },
                    }
                    self.client._request("addInventoryProduct", update_payload)
                    logger.info(f"Produto {sku_novo} atualizado com EAN {ean_final} e Ficha Técnica.")

            except Exception as exc:
                logger.error(f"Erro ao criar variação {anuncio.referencia} (SKU {sku_novo}): {exc}")
                if "Falha ao obter ID" in str(exc) or "API Baselinker falhou" in str(exc):
                    self._reciclar_numero(sku_novo)

        return {
            "sucesso": True,
            "skus":    skus_criados,
            "ids":     ids_criados,
            "msg":     f"Grade criada com {len(skus_criados)} anúncios no BaseLinker.",
        }

    def executar_criacao(
        self,
        pid: str,
        sku_origem: str,
        sku_desejado: Optional[str] = None,
        sku_secundario: Optional[str] = None,
        category_id_ml: Optional[str] = None,
    ) -> Dict:
        """
        Cria um kit no BaseLinker.
        - sku_secundario: se informado, cria um Kit Misto com 2 produtos diferentes.
        """
        logger.warning(f"Criando kit -> PID={pid} | SKU origem={sku_origem} | Misto={bool(sku_secundario)}")

        dados = self._baixar_dados_produto(pid)
        if not dados:
            return {
                "sucesso": False,
                "msg": f"Falha ao baixar dados do PID={pid}.",
                "erro_detalhe": "Produto não encontrado no BaseLinker.",
            }

        # Trava de Segurança: Validação de dados essenciais
        erro_validacao = self._validar_dados_essenciais(dados)
        if erro_validacao:
            return {"sucesso": False, "msg": erro_validacao}

        if not category_id_ml or category_id_ml == "979589":
            return {"sucesso": False, "msg": "Categoria do Mercado Livre não informada. Por favor, forneça a árvore de categoria no formulário do painel."}

        dados_secundario = None
        pid_secundario   = None
        if sku_secundario:
            pid_secundario = self.buscar_pid_por_sku(sku_secundario)
            if not pid_secundario:
                return {
                    "sucesso": False,
                    "msg": f"SKU secundário {sku_secundario} não encontrado.",
                    "erro_detalhe": f"SKU {sku_secundario} não encontrado no BaseLinker.",
                }
            dados_secundario = self._baixar_dados_produto(pid_secundario)
            if not dados_secundario:
                return {
                    "sucesso": False,
                    "msg": f"Falha ao baixar dados do SKU secundário {sku_secundario}.",
                    "erro_detalhe": "Produto secundário não encontrado.",
                }
            
            # Validação para produto secundário
            erro_validacao_sec = self._validar_dados_essenciais(dados_secundario)
            if erro_validacao_sec:
                return {"sucesso": False, "msg": f"Secundário: {erro_validacao_sec}"}

        category_id_bl = self.sincronizar_categoria_ml_para_baselinker(category_id_ml)

        # Gera conteúdo via IA antes de alocar SKU
        try:
            tf = dados.get("text_fields", {}) or {}
            nome_original_prod = tf.get("name") or dados.get("name") or ""
            descricao_original = tf.get("description") or dados.get("description") or ""
            features_base      = self._extrair_features(dados)
            lista_obrigatorios = self._buscar_atributos_obrigatorios_ml(category_id_ml, features_base, dados)
            imgs_map_kit       = self._extrair_imagens(dados)
            urls_imgs_kit      = [v.removeprefix("url:") for v in imgs_map_kit.values() if v.startswith("url:http")]
            ean_val_kit        = str((features_base or {}).get("EAN") or (features_base or {}).get("ean") or (features_base or {}).get("Código universal de produto") or "")
            marca_val_kit      = str((features_base or {}).get("Marca") or (features_base or {}).get("marca") or "")

            conteudo_ia = self.seo.gerar_conteudo(
                nome_original_prod,
                descricao_original,
                features_base,
                e_misto=bool(dados_secundario),
                atributos_obrigatorios=lista_obrigatorios,
                gera_grade=False,
                category_id_ml=category_id_ml,
                imagens_urls=urls_imgs_kit,
                ean=ean_val_kit,
                marca=marca_val_kit,
            )
        except Exception as exc:
            return {"sucesso": False, "msg": f"Erro crítico na geração de conteúdo pela IA: {exc}"}

        # Trava de Segurança: Valida se a IA gerou título e descrição válidos
        erro_conteudo = self._validar_conteudo_ia(conteudo_ia, nome_original_prod)
        if erro_conteudo:
            return {"sucesso": False, "msg": erro_conteudo}

        for tentativa in range(1, 6):
            sku_final = self._obter_proximo_sku(sku_desejado)
            if not sku_final:
                return {
                    "sucesso": False,
                    "msg": "SKU indisponível.",
                    "erro_detalhe": "DB retornou NULL em get_next_kit_sku().",
                }

            try:
                payload = self._montar_payload(
                    dados, sku_final, pid, dados_secundario, pid_secundario, category_id_bl, category_id_ml
                )
            except Exception as exc:
                return {"sucesso": False, "msg": f"Erro ao montar payload: {exc}", "erro_detalhe": str(exc)}

            try:
                id_kit = self._enviar_para_baselinker(payload)
            except Exception as exc:
                erro = str(exc).lower()
                if any(t in erro for t in ["exist", "duplicate", "unique", "conflict"]) and not sku_desejado:
                    self._reciclar_numero(sku_final)
                    continue
                self._reciclar_numero(sku_final)
                return {"sucesso": False, "msg": f"Erro API BaseLinker: {exc}", "erro_detalhe": str(exc)}

            if id_kit:
                self._salvar_historico(sku_origem, pid, sku_final, id_kit)
                tipo = "Kit Misto" if sku_secundario else "Kit"
                return {
                    "sucesso":  True,
                    "sku_novo": sku_final,
                    "id_kit":   id_kit,
                    "tipo":     tipo,
                    "msg":      f"{tipo} {sku_final} criado com sucesso! (ID: {id_kit})",
                }

        return {"sucesso": False, "msg": "Falha após 5 tentativas.", "erro_detalhe": "Verifique o BaseLinker."}

    # ── Utilitários ───────────────────────────────────────────────────────────

    def sincronizar_categoria_ml_para_baselinker(self, category_id_ml: Optional[str]) -> int:
        if not category_id_ml or category_id_ml == "979589":
            return self.ID_CATEGORIA_KITS

        try:
            ml_client = MercadoLivreClient()
            data = ml_client._get_auth(f"categories/{category_id_ml}")
            if not data:
                return self.ID_CATEGORIA_KITS

            caminho = data.get("path_from_root", [])
            if not caminho:
                return self.ID_CATEGORIA_KITS

            categorias_bl = self.client.get_inventory_categories(self.ID_INVENTARIO)
            mapa_bl = {
                (c.get("parent_id", 0), c.get("name", "").strip().lower()): c.get("category_id")
                for c in categorias_bl
            }

            parent_id_atual = 0
            for nivel in caminho:
                nome_nivel = nivel.get("name", "").strip()
                if not nome_nivel:
                    continue
                chave = (parent_id_atual, nome_nivel.lower())
                cat_id = mapa_bl.get(chave)

                if not cat_id:
                    logger.info(f"Criando categoria '{nome_nivel}' (pai: {parent_id_atual}) no BaseLinker...")
                    novo_id = self.client.addInventoryCategory(self.ID_INVENTARIO, nome_nivel, parent_id_atual)
                    if not novo_id:
                        logger.error(f"Falha ao criar '{nome_nivel}' no BL.")
                        return parent_id_atual if (isinstance(parent_id_atual, int) and parent_id_atual > 0) else self.ID_CATEGORIA_KITS
                    cat_id = novo_id
                    mapa_bl[chave] = cat_id

                parent_id_atual = cat_id

            return parent_id_atual if (isinstance(parent_id_atual, int) and parent_id_atual > 0) else self.ID_CATEGORIA_KITS

        except Exception as exc:
            logger.error(f"Erro sincronizando categoria {category_id_ml}: {exc}")
            return self.ID_CATEGORIA_KITS

    def _get_vendas_60_dias(self, sku: str) -> int:
        try:
            from pedidos.models import ItemPedido
            from django.utils import timezone
            from datetime import timedelta
            from django.db.models import Sum

            sessenta_dias_atras = timezone.now() - timedelta(days=60)
            qs = ItemPedido.objects.filter(
                sku=sku,
                pedido__data_criacao__gte=sessenta_dias_atras,
            )
            vendas = qs.aggregate(total=Sum('quantidade'))['total'] or 0
            return int(vendas)
        except Exception as exc:
            logger.error(f"Erro ao contar vendas para {sku}: {exc}")
            return 0

    def _sku_existe_no_banco_local(self, sku: str) -> bool:
        """Verifica se o SKU já existe em qualquer tabela do banco de dados local."""
        if not sku:
            return False
        sku_limpo = str(sku).strip()
        try:
            if HistoricoKit.objects.filter(Q(sku_kit=sku_limpo) | Q(sku_origem=sku_limpo)).exists():
                return True
            from produtos.models import Produto
            if Produto.objects.filter(sku=sku_limpo).exists():
                return True
            from ..models.baselinker_kit_sku import BaselinkerKitSku
            if sku_limpo.isdigit() and BaselinkerKitSku.objects.filter(sku=int(sku_limpo)).exists():
                return True
            if GtinKit.objects.filter(Q(sku_kit=sku_limpo) | Q(sku_origem=sku_limpo)).exists():
                return True
            from anuncios.models import FilaAnuncio
            if FilaAnuncio.objects.filter(Q(sku_principal=sku_limpo) | Q(sku_secundario=sku_limpo)).exists():
                return True
            return False
        except Exception as exc:
            logger.error("Falha ao checar SKU %s no banco local: %s", sku_limpo, exc, exc_info=True)
            return True

    def _sku_existe_no_baselinker(self, sku: str) -> bool:
        """Verifica em tempo real se o SKU já existe no catálogo do BaseLinker."""
        if not sku:
            return False
        sku_limpo = str(sku).strip()
        try:
            pid = self.buscar_pid_por_sku(sku_limpo)
            if pid:
                logger.warning("SKU %s já cadastrado no BaseLinker com PID %s.", sku_limpo, pid)
                if sku_limpo.isdigit():
                    from ..models.baselinker_kit_sku import BaselinkerKitSku
                    BaselinkerKitSku.objects.get_or_create(sku=int(sku_limpo), defaults={"product_id": str(pid)})
                return True
            return False
        except Exception as exc:
            logger.error("Falha ao verificar SKU %s no BaseLinker: %s", sku_limpo, exc, exc_info=True)
            return True

    def _get_next_kit_sku_raw(self) -> Optional[int]:
        """Executa a alocação atômica via função PL/pgSQL get_next_kit_sku."""
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT get_next_kit_sku()")
                row = cursor.fetchone()
                return int(row[0]) if row and row[0] is not None else None
        except Exception as exc:
            logger.error("DB get_next_kit_sku: %s", exc, exc_info=True)
            return None

    def _get_next_kit_sku_atomico(self) -> Optional[int]:
        """Método de alocação atômica individual de SKU."""
        return self._get_next_kit_sku_raw()

    def _obter_proximo_sku(self, sku_desejado: Optional[str]) -> Optional[str]:
        """Aloca o próximo SKU garantindo unicidade no banco local e no BaseLinker."""
        if sku_desejado:
            sku_limpo = str(sku_desejado).strip()
            if self._sku_existe_no_banco_local(sku_limpo) or self._sku_existe_no_baselinker(sku_limpo):
                logger.error("SKU desejado %s já está em uso.", sku_limpo)
                return None
            return sku_limpo

        for tentativa in range(1, 201):
            candidato_int = self._get_next_kit_sku_raw()
            if candidato_int is None:
                logger.error("DB get_next_kit_sku retornou NULL na tentativa %s.", tentativa)
                return None

            candidato_str = str(candidato_int)

            if candidato_int < 50000:
                logger.warning("SKU %s descartado por estar abaixo de 50000.", candidato_str)
                continue

            if self._sku_existe_no_banco_local(candidato_str):
                logger.warning("SKU %s já existe no banco de dados local. Alocando próximo...", candidato_str)
                continue

            if self._sku_existe_no_baselinker(candidato_str):
                logger.warning("SKU %s já existe no BaseLinker. Alocando próximo...", candidato_str)
                continue

            return candidato_str

        logger.error("Excedido limite de tentativas para alocar SKU inédito.")
        return None

    def _enviar_para_baselinker(self, payload: Dict) -> Optional[str]:
        resp = self.client._request("addInventoryProduct", payload)
        pid  = resp.get("product_id")
        if not pid:
            raise Exception(f"Sem product_id na resposta: {resp}")
        return str(pid)

    def _salvar_historico(self, sku_origem: str, id_origem: str, sku_kit: str, id_kit: str) -> None:
        try:
            HistoricoKit.objects.create(
                sku_origem=sku_origem,
                id_origem=id_origem,
                sku_kit=sku_kit,
                id_kit=id_kit,
            )
        except Exception as exc:
            logger.error(f"Historico ({sku_origem}->{sku_kit}): {exc}")

    def _reciclar_numero(self, sku: str) -> None:
        """Insere SKU em NumeroReciclado apenas se >= 50000 e não utilizado no DB ou BaseLinker."""
        if not sku or not str(sku).isdigit():
            return
        num = int(sku)
        if num < 50000:
            logger.info("Ignorando reciclagem do SKU %s por ser < 50000.", sku)
            return
        if self._sku_existe_no_banco_local(str(num)) or self._sku_existe_no_baselinker(str(num)):
            logger.info("Ignorando reciclagem do SKU %s pois já está em uso.", sku)
            return
        try:
            NumeroReciclado.objects.get_or_create(numero=num)
        except Exception as exc:
            logger.warning("Falha ao reciclar SKU %s: %s", sku, exc)

    def executar_rollback_grade(self, sku_origem: str) -> Dict:
        """
        Deleta os produtos criados na grade (a partir de HistoricoKit),
        limpa os GTINs locais associados e recicla os SKUs utilizados.
        """
        logger.warning(f"Iniciando ROLLBACK completo para SKU Origem={sku_origem}")
        historicos = HistoricoKit.objects.filter(sku_origem=sku_origem)
        
        if not historicos.exists():
            return {
                "sucesso": False,
                "msg": f"Nenhum kit no histórico encontrado para o SKU de origem {sku_origem}."
            }

        deletados_bl = 0
        skus_reciclados = []
        erros = []

        for h in historicos:
            try:
                # Deleta o produto do BaseLinker
                resp_del = self.client._request("deleteInventoryProduct", {
                    "inventory_id": self.ID_INVENTARIO,
                    "product_id": int(h.id_kit),
                })
                if resp_del.get("status") == "SUCCESS":
                    deletados_bl += 1
                else:
                    erros.append(f"Erro ao deletar ID {h.id_kit} no BaseLinker: {resp_del.get('error_message')}")
            except Exception as e:
                erros.append(f"Falha ao deletar ID {h.id_kit} no BaseLinker: {e}")

            # Recicla o SKU
            self._reciclar_numero(h.sku_kit)
            skus_reciclados.append(h.sku_kit)

        # Inativa os GTINs gerados para este lote na API da GS1 Brasil antes de apagar registros
        from integracoes.gs1.client import get_gs1_client
        gs1 = get_gs1_client()
        gtins = GtinKit.objects.filter(sku_origem=sku_origem).exclude(gtin='PENDENTE').exclude(gtin='').values_list('gtin', flat=True).distinct()
        for g in gtins:
            try:
                logger.warning(f"Chamando inativação GS1 para o GTIN {g} devido ao Rollback.")
                gs1.inativar_ou_excluir_produto(g)
            except Exception as e:
                logger.error(f"Erro ao inativar GTIN {g} na GS1 durante rollback: {e}")

        # Deleta registros de histórico
        historicos.delete()

        # Deleta registros de GTIN local (GtinKit)
        gtins_deletados, _ = GtinKit.objects.filter(sku_origem=sku_origem).delete()

        msg = f"Rollback concluído. Deletados {deletados_bl} anúncios no BaseLinker e {gtins_deletados} GTINs locais limpos. SKUs reciclados: {', '.join(skus_reciclados)}."
        logger.warning(msg)
        if erros:
            logger.error(f"Erros encontrados no rollback: {'; '.join(erros)}")

        return {
            "sucesso": True,
            "msg": msg,
            "deletados_bl": deletados_bl,
            "gtins_deletados": gtins_deletados,
            "erros": erros,
        }

    def executar_exclusao_individual(self, id_kit: str, sku_kit: str) -> Dict:
        """
        Deleta um único produto do BaseLinker, remove do histórico local,
        limpa o GTIN associado a esse SKU de kit e recicla o SKU.
        """
        logger.warning(f"Iniciando exclusão individual -> SKU Kit={sku_kit} | ID Kit={id_kit}")
        
        # Deleta do BaseLinker
        sucesso_bl = False
        erro_bl = None
        try:
            resp_del = self.client._request("deleteInventoryProduct", {
                "inventory_id": self.ID_INVENTARIO,
                "product_id": int(id_kit),
            })
            if resp_del.get("status") == "SUCCESS":
                sucesso_bl = True
            else:
                erro_bl = resp_del.get("error_message")
        except Exception as e:
            erro_bl = str(e)

        # Inativa o GTIN correspondente na GS1 Brasil se ele existir
        from integracoes.gs1.client import get_gs1_client
        gs1 = get_gs1_client()
        gtins_kit = GtinKit.objects.filter(sku_kit=sku_kit).exclude(gtin='PENDENTE').exclude(gtin='').values_list('gtin', flat=True)
        for g in gtins_kit:
            try:
                logger.warning(f"Chamando inativação GS1 para o GTIN {g} devido à exclusão individual.")
                gs1.inativar_ou_excluir_produto(g)
            except Exception as e:
                logger.error(f"Erro ao inativar GTIN {g} na GS1 durante exclusão: {e}")

        # Recicla o SKU
        self._reciclar_numero(sku_kit)

        # Deleta do histórico local
        HistoricoKit.objects.filter(id_kit=id_kit).delete()

        # Deleta do banco de GTINs local (GtinKit)
        GtinKit.objects.filter(sku_kit=sku_kit).delete()

        if sucesso_bl:
            msg = f"Produto {sku_kit} excluído com sucesso do BaseLinker e GTIN local removido."
            return {"sucesso": True, "msg": msg}
        else:
            msg = f"GTIN local e histórico de {sku_kit} limpos, mas falhou ao excluir no BaseLinker: {erro_bl}."
            return {"sucesso": True, "msg": msg}