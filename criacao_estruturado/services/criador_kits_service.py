from __future__ import annotations

import logging
import time
import uuid
import re
import json
from typing import Dict, List, Optional
from decimal import Decimal

from django.db import connection
from django.conf import settings
from decouple import config

from integracoes.baselinker.client import BaselinkerClient
from integracoes.mercadolivre.client import MercadoLivreClient
from integracoes.gs1.client import get_gs1_client
from integracoes.gemini import GeminiClient
from integracoes.groq import GroqClient

from ..models.historico_kit import HistoricoKit
from ..models.gtin_kit import GtinKit
from ..models.numero_reciclado import NumeroReciclado
from precificacao.services.calculadora import Calculadora
from precificacao.models import RegrasPrecificacao
from categorias.models.termos_proibidos import TermosProibidos

logger = logging.getLogger("gerador_kits")

PRICE_IDS: List[str] = [
    "53516", "24908", "25461", "25462", "25463", "25464", "25465",
    "25466", "25467", "25468", "25469", "28598", "29479", "29723",
    "31771", "31772", "40117", "40403", "40579", "40751", "40998",
    "41205", "41207", "41219", "48026",
]


# =============================================================================
# UTILITÃRIOS DE PÃS-PROCESSAMENTO (sem dependÃªncia de IA)
# =============================================================================

# SeÃ§Ãµes obrigatÃ³rias da descriÃ§Ã£o â usadas para validar a resposta da IA
SECOES_OBRIGATORIAS = [
    "SOBRE O PRODUTO",
    "BENEFÃCIOS",
    "DIFERENCIAIS DO PRODUTO",
    "DÃVIDAS FREQUENTES",
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
    """Primeira letra de cada palavra maiÃºscula, resto minÃºsculo. Preserva siglas."""
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
    Normaliza o texto para comparaÃ§Ãµes robustas e flexÃveis de cabeÃ§alhos de seÃ§Ã£o:
    - Transforma em caixa baixa
    - Remove marcas de markdown (*, #, :, -, _, etc.)
    - Remove acentos/diacrÃticos
    - Normaliza mÃºltiplos espaÃ§os
    """
    import unicodedata
    import re
    texto = (texto or "").lower()
    # Remove marcas de markdown e pontuaÃ§Ãµes de tÃtulo
    texto = re.sub(r'[*#:\-_]', ' ', texto)
    # Remove acentos
    texto_norm = unicodedata.normalize('NFD', texto)
    texto_sem_acentos = "".join(c for c in texto_norm if unicodedata.category(c) != 'Mn')
    return " ".join(texto_sem_acentos.split())


# VersÃµes normalizadas das seÃ§Ãµes para comparaÃ§Ã£o resiliente
SECOES_OBRIGATORIAS_NORM = {
    "SOBRE O PRODUTO": "sobre o produto",
    "BENEFÃCIOS": "beneficios",
    "DIFERENCIAIS DO PRODUTO": "diferenciais do produto",
    "DÃVIDAS FREQUENTES": "duvidas frequentes",
    "IDEAL PARA": "ideal para",
}


def validar_descricao(desc: str) -> bool:
    """Verifica se todas as seÃ§Ãµes obrigatÃ³rias estÃ£o presentes na descriÃ§Ã£o de forma robusta e flexÃvel."""
    desc_norm = normalizar_texto_validador(desc)
    return all(norm_val in desc_norm for norm_val in SECOES_OBRIGATORIAS_NORM.values())


def normalizar_descricao(desc: str) -> str:
    """
    Garante que a descriÃ§Ã£o sempre comece pela seÃ§Ã£o correta,
    removendo qualquer lixo antes de 'SOBRE O PRODUTO'.
    Suporta variaÃ§Ãµes de caixa e marcas de markdown.
    """
    desc_strip = desc.strip()
    # Procura 'SOBRE O PRODUTO' de forma case-insensitive e flexÃvel em relaÃ§Ã£o a markdown/espaÃ§os/pontuaÃ§Ãµes
    match = re.search(r'(?:[#*]*\s*)?(sobre\s+o\s+produto)(?:\s*[#*:]*)?', desc_strip, re.IGNORECASE)
    if match:
        idx = match.start()
        return desc_strip[idx:].strip()
    return desc_strip


# =============================================================================
# SEO GENERATOR (Orquestrador de Agentes Especializados)
# =============================================================================

class SEOGenerator:
    """
    Orquestrador de Agentes Especializados para SEO e Compliance.
    Usa Gemini 2.5 Flash para Compliance, TÃtulos e DescriÃ§Ã£o.
    Usa Llama 3.1/3.3 (Groq) para Ficha TÃ©cnica e Keywords.

    MUDANÃAS v2.1:
    - Alterado TÃtulos e DescriÃ§Ã£o para Gemini 2.5 Flash (Google AI Studio).
    - Temperatura 0.0 em todas as chamadas de geraÃ§Ã£o.
    - ValidaÃ§Ã£o de caracteres feita 100% em Python.
    - DescriÃ§Ã£o usa template fixo com marcadores.
    """

    # -------------------------------------------------------------------------
    # AGENTE 1 â COMPLIANCE (Gemini)
    # -------------------------------------------------------------------------
    PROMPT_COMPLIANCE = """VocÃª Ã© um Auditor de Compliance SÃªnior do Mercado Livre Brasil com profundo conhecimento nas polÃticas de 2024/2025.
Sua Ãºnica funÃ§Ã£o Ã© verificar se um produto pode ser anunciado na plataforma antes de qualquer geraÃ§Ã£o de conteÃºdo.

====================================
CATEGORIAS PROIBIDAS - Bloqueie imediatamente
====================================

FINANCEIRO
- Moedas, cÃ©dulas antigas/comemorativas para venda como "investimento"
- Cheques, cartÃµes clonados, maquinetas nÃ£o homologadas
- Criptomoedas, tokens, NFTs

DIGITAL / SOFTWARE
- Contas de streaming, jogos ou plataformas (Netflix, Spotify, etc.)
- Senhas, acessos, convites
- Softwares piratas, chaves de ativaÃ§Ã£o nÃ£o oficiais
- Chips desbloqueados, IPTV pirata

SEGURANÃA E SAÃDE
- Armas de fogo, muniÃ§Ãµes, simulacros funcionais
- Explosivos, fogos de artifÃcio categoria 3 e 4
- Produtos controlados pela ANVISA sem registro vÃ¡lido
- Medicamentos sob prescriÃ§Ã£o, anabolizantes, hormÃ´nios
- SubstÃ¢ncias psicoativas, entorpecentes
- Equipamentos de vigilÃ¢ncia para uso ilegal (grampos, rastreadores ocultos)

PROPRIEDADE INTELECTUAL
- RÃ©plicas, falsificaÃ§Ãµes, imitaÃ§Ãµes de marcas registradas
- Produtos com termo "inspired by", "estilo de", "modelo igual ao"
- Itens sem nota fiscal de origem comprovÃ¡vel para marcas premium

NÃO-PRODUTO
- ServiÃ§os, consultorias, freelances, cursos (exceto se plataforma habilitada)
- CurrÃculos, templates de currÃculo
- Promessas de resultado financeiro ou saÃºde

ADULTO
- ConteÃºdo sexual explÃcito fora da categoria adulto habilitada
- Produtos erÃ³ticos sem a categorizaÃ§Ã£o correta

ANIMAIS E MEIO AMBIENTE
- EspÃ©cies silvestres, trÃ¡fico de animais
- Produtos de origem animal sem certificado IBAMA
- Madeira nÃ£o certificada

====================================
ATENÃÃO - Falsos Positivos Comuns (NÃO bloqueie)
====================================
- "Kit faca churrasco" -> PERMITIDO (utensÃlio domÃ©stico)
- "Kit 2 copos Stanley" -> PERMITIDO (kit legÃtimo de produto permitido)
- "Kit 3 unidades faca de cozinha" -> PERMITIDO
- "Stanley original" -> PERMITIDO (marca legÃtima com palavra "original")
- "Garrafa tÃ©rmica 500ml" -> PERMITIDO
- "RÃ©plica design vintage" -> BLOQUEADO (termo rÃ©plica = falsificaÃ§Ã£o)
- "Suplemento para emagrecer 10kg garantido" -> BLOQUEADO (promessa de resultado)
- "Canivete camping" -> PERMITIDO (ferramenta de uso legÃtimo)
- "Faca tÃ¡tica militar" -> AVALIAR CONTEXTO - se for utensÃlio de camping/pesca, permitido

====================================
INSTRUÃÃO DE ANÃLISE
====================================
1. Leia o nome e a descriÃ§Ã£o do produto
2. Verifique contra TODAS as categorias proibidas
3. Aplique o teste de falso positivo
4. Se houver dÃºvida genuÃna, classifique como "revisar" para anÃ¡lise humana

Responda EXCLUSIVAMENTE em JSON, sem texto adicional, sem markdown:

Se aprovado:
{"status": "ok", "observacao": "breve nota se relevante ou null"}

Se bloqueado:
{"status": "bloqueado", "motivo": "ExplicaÃ§Ã£o clara em 1 frase", "categoria": "Nome da categoria proibida"}

Se precisar de revisÃ£o humana:
{"status": "revisar", "motivo": "Motivo da dÃºvida em 1 frase", "categoria": "Categoria suspeita"}"""

    # -------------------------------------------------------------------------
    # AGENTE 2A â TÃTULO UNITÃRIO (Groq, temperatura 0.0)
    # -------------------------------------------------------------------------
    PROMPT_TITULO = """Você é um Especialista em SEO para Mercado Livre Brasil.
Sua única tarefa é gerar um ttulo neutro e descritivo para o produto fornecido.

====================================
ESTRUTURA RÍGIDA E OBRIGATÓRIA (NADA MAIS DEVE SER ACRESCENTADO)
====================================
Gere o ttulo rigorosamente seguindo apenas estes quatro elementos na ordem abaixo:
1. [Quantidade] (apenas se for kit de mais de 1 unidade, ex: "Kit 2", "Kit 3". Para unitário, deixe vazio).
2. [Tipo de Produto] (categoria/nome objetivo do produto, ex: "Cola Vinil", "Limpa Pedras", "Suporte TV", "Tira Manchas").
3. [Formato / Especificação / Modelo / Compatibilidade] (especificação útil que identifique o item de forma real, ex: "17g", "Fixo 14 a 101 Polegadas", "Lngua", "para Reboco", "para Madeira", "para TV"). Use "para" ou "compattivel com" se fizer sentido.
4. [Marca] (marca oficial e modelo, ex: "Tekbond", "Sanol Pro", "Promabond", "Clear").

====================================
REGRAS CRÍTICAS DE CONTRAPARTIÇÃO (EVITE E BLOQUEIE):
====================================
- O ttulo deve ter no MÁXIMO 60 caracteres.
- NUNCA use adjetivos ou termos comerciais/subjetivos ("Premium", "Clássico", "Forte", "Eficaz", "Poderoso", "Potente", "Excelente", "Original", "Garantia", "Envio Rápido", etc.).
- NUNCA use promessas de resultado, cura, emagrecimento ou tratamento.
- NUNCA use repetições, emojis, pontuação excessiva ou caracteres especiais (- , . ! ? % & @ # / [ ] ( )).
- Primeira letra maiúscula de cada palavra, restante em minúsculo (exceto siglas).

Responda EXCLUSIVAMENTE em JSON:
{"titulo": "ttulo aqui"}"""

    # -------------------------------------------------------------------------
    # AGENTE 2B  GRADE DE 8 TÍTULOS (Groq, temperatura 0.0)
    # -------------------------------------------------------------------------
    PROMPT_TITULO_KITS = """Você é um Especialista em SEO para Mercado Livre Brasil.
Sua tarefa é gerar 8 ttulos base únicos e altamente otimizados seguindo estritamente a estrutura neutra e descritiva.

====================================
ESTRUTURA RÍGIDA E OBRIGATÓRIA (NADA MAIS DEVE SER ACRESCENTADO)
====================================
Gere os ttulos rigorosamente com base apenas nos elementos abaixo:
1. [Tipo de Produto] (categoria/nome objetivo do produto, ex: "Cola Vinil", "Limpa Pedras", "Suporte TV", "Tira Manchas").
2. [Formato / Especificação / Modelo / Compatibilidade] (especificação útil que identifique o item de forma real, ex: "17g", "Fixo 14 a 101 Polegadas", "Lngua", "para Reboco", "para Madeira", "para TV"). Use "para" ou "compattivel com" se fizer sentido.
3. [Marca] (marca oficial e modelo, ex: "Tekbond", "Sanol Pro", "Promabond", "Clear").

*ATENÇÃO: NÃO inclua quantidades (ex: "Kit 2", "3 Unidades") na grade de ttulos. O sistema injetará as quantidades automaticamente no final.

====================================
REGRAS CRÍTICAS (EVITE E BLOQUEIE):
====================================
- Cada ttulo deve ter no máximo 55 a 60 caracteres.
- NUNCA use adjetivos ou termos comerciais/subjetivos ("Premium", "Clássico", "Forte", "Eficaz", "Poderoso", "Potente", "Excelente", "Original", "Garantia", "Envio Rápido", etc.).
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
    # AGENTE 4  FICHA TÉCNICA / ATRIBUTOS (Groq)
    # -------------------------------------------------------------------------
    PROMPT_TECH = """VocÃª Ã© um Analista de Dados TÃ©cnicos especialista em fichas de produto
para e-commerce e marketplaces brasileiros. Sua funÃ§Ã£o Ã© extrair, inferir
e padronizar atributos tÃ©cnicos a partir de qualquer tipo de texto sobre
produtos (tÃtulo, descriÃ§Ã£o, bullet points, nome de arquivo, SKU, etc.).

====================================
ETAPA 0 â DETECÃÃO DE MODELO
====================================
Antes de preencher os campos solicitados, identifique o MODELO do produto.
O modelo Ã© o identificador especÃfico que diferencia variantes de uma mesma
linha (ex: "Classic 1.0", "AX-500", "Pro Max 64GB").

EstratÃ©gias de detecÃ§Ã£o (aplique nesta ordem):

  A) CÃDIGO ALFANUMÃRICO no tÃtulo ou SKU
     PadrÃ£o: sequÃªncia com letras + nÃºmeros separados por hÃfen, ponto ou
     espaÃ§o apÃ³s a marca.
     Exemplos:
       "Stanley ADVC-10" â Modelo: "ADVC-10"
       "Tramontina 29899-083" â Modelo: "29899-083"
       "Nike Air Max 90" â Modelo: "Air Max 90"

  B) NOME DE LINHA / SÃRIE explÃcito
     Palavras que denotam sÃ©rie apÃ³s a marca ou categoria.
     Exemplos:
       "Garrafa Stanley Classic" â Modelo: "Classic"
       "Faca Tramontina Century" â Modelo: "Century"

  C) ESPECIFICAÃÃO TÃCNICA IDENTIFICADORA
     Quando o modelo Ã© descrito sÃ³ por capacidade/tamanho e nÃ£o hÃ¡ nome.
     Exemplos:
       "Garrafa Inox 750ml" (sem cÃ³digo) â Modelo: "750 ml (inferido)"

  D) NÃO IDENTIFICÃVEL
     Se nenhuma estratÃ©gia encontrar o modelo â omita a chave "Modelo"
     (ou use "NÃ£o informado" se marcado como [OBRIGATÃRIO]).

Sempre inclua "Modelo" como primeiro campo do JSON de atributos.

====================================
ETAPA 1 â HIERARQUIA DE PREENCHIMENTO
====================================
Para cada campo da lista "Campos Solicitados":

  1. EXPLÃCITO  â valor mencionado diretamente no texto.
                  Extraia e padronize conforme seÃ§Ã£o PADRONIZAÃÃO.

  2. INFERÃVEL  â dedutÃvel com alta confianÃ§a a partir do contexto.
                  Acrescente " (inferido)" ao valor.
                  EXCEÃÃO: campos com lista de Valores Permitidos â
                  escolha o valor mais adequado da lista SEM sufixo.

  3. ENRIQUECÃVEL â vocÃª conhece o produto/marca e pode preencher com
                  conhecimento de mercado (ex: marca conhecida, voltagem
                  padrÃ£o do segmento).
                  Acrescente " (conhecimento de mercado)" ao valor.
                  EXCEÃÃO: mesma regra das listas de valores permitidos.

  4. AUSENTE    â omita a chave do JSON.
                  EXCEÃÃO: campos [OBRIGATÃRIO] â use "NÃ£o informado".

Prioridade de confianÃ§a: ExplÃcito > InferÃvel > EnriquecÃvel > Ausente.

====================================
ETAPA 2 â VALIDAÃÃO ANTES DE RETORNAR
====================================
Antes de gerar o JSON final, faÃ§a internamente estas verificaÃ§Ãµes:

  â O campo "Modelo" estÃ¡ presente e Ã© o primeiro?
  â Cada chave bate exatamente com o nome em "Campos Solicitados"?
  â Campos com Valores Permitidos usam exatamente um valor da lista?
  â Nenhum campo contÃ©m null, "", "N/A", "Desconhecido" ou similar?
  â Campos ausentes sem [OBRIGATÃRIO] foram omitidos (nÃ£o retornados)?
  â Medidas tÃªm nÃºmero + espaÃ§o + unidade?
  â NÃ£o hÃ¡ EAN, GTIN ou CÃ³digo de barras no JSON?

====================================
REGRAS CRÃTICAS
====================================
R1. Use EXATAMENTE os nomes de atributos fornecidos em "Campos Solicitados".
R2. Campos com "Valores Permitidos": escolha EXATAMENTE um valor da lista.
    Nunca combine, abrevie ou invente termos fora da lista.
R3. Conflito texto Ã lista â priorize a lista (valor mais prÃ³ximo
    semanticamente).
R4. NUNCA extraia: "CÃ³digo de barras", "EAN", "GTIN" ou "Motivo de GTIN
    vazio". O sistema preenche a identificaÃ§Ã£o automaticamente via API.
R5. NUNCA invente valores sem base no texto, inferÃªncia lÃ³gica clara
    ou conhecimento de mercado verificÃ¡vel.

====================================
PADRONIZAÃÃO DE VALORES
====================================
- Idioma       : PortuguÃªs brasileiro.
                 â "Preto"  â "Black" | â "Azul marinho"  â "Navy"
- Medidas      : nÃºmero + espaÃ§o + unidade â "500 ml", "15 cm", "2 kg"
- Booleanos    : "Sim" ou "NÃ£o"
- Voltagem     : "110V" | "220V" | "Bivolt" | "NÃ£o se aplica"
- Marcas       : capitalizaÃ§Ã£o correta â "Stanley", "3M", "Tramontina"
- Cores        : primeira letra maiÃºscula â "Preto", "Inox", "Chumbo"
- Modelo       : preserve o cÃ³digo original (maiÃºsculas, hÃfens, pontos)

====================================
EXEMPLO COMPLETO
====================================
Campos Solicitados:
  - Material [OBRIGATÃRIO]
    (Valores Permitidos: "Inox", "PlÃ¡stico", "Vidro", "AlumÃnio")
  - Capacidade [OBRIGATÃRIO]
  - Cor
  - Voltagem
  - Peso

Texto de entrada:
  "Garrafa TÃ©rmica Stanley Classic 1.0 AÃ§o Inox 750ml Grafite ADVC-10"

SaÃda esperada:
{
  "atributos": {
    "Modelo": "ADVC-10",
    "Material": "Inox",
    "Capacidade": "750 ml",
    "Cor": "Grafite",
    "Voltagem": "NÃ£o se aplica",
    "Peso": "NÃ£o informado"
  }
}

Notas do exemplo:
- "Modelo" detectado pela estratÃ©gia A (cÃ³digo alfanumÃ©rico "ADVC-10").
- "Material" veio da lista â sem sufixo.
- "Cor" estava explÃcita â sem sufixo.
- "Voltagem" preenchida por conhecimento de mercado (garrafa = sem
  elÃ©trica) â valor da lista, sem sufixo.
- "Peso" ausente + [OBRIGATÃRIO] â "NÃ£o informado".
- "Classic 1.0" ignorado no Modelo pois "ADVC-10" Ã© mais especÃfico
  (estratÃ©gia A tem prioridade sobre B).

====================================
FORMATO DE SAÃDA
====================================
Responda EXCLUSIVAMENTE em JSON vÃ¡lido, sem texto adicional,
sem markdown, sem comentÃ¡rios:
{
  "atributos": {
    "Modelo": "...",
    "NOME_EXATO_DO_CAMPO": "Valor Padronizado"
  }
}"""

    # -------------------------------------------------------------------------
    # AGENTE 5 â PALAVRAS-CHAVE / SEO (Groq)
    # -------------------------------------------------------------------------
    PROMPT_KEYWORDS = """VocÃª Ã© um Especialista em Search Intelligence para marketplaces brasileiros com foco em Mercado Livre e Google Shopping.
Sua funÃ§Ã£o Ã© identificar os termos de busca mais relevantes para maximizar o ranqueamento orgÃ¢nico do anÃºncio.

====================================
PRINCÃPIOS DO ALGORITMO DO ML
====================================
O Mercado Livre ranqueia com base em:
1. RelevÃ¢ncia do tÃtulo com o termo buscado
2. Velocidade de vendas e taxa de conversÃ£o
3. ReputaÃ§Ã£o do vendedor
4. Qualidade e completude do anÃºncio
5. Palavras-chave presentes na descriÃ§Ã£o

====================================
LÃGICA DE KITS - OBRIGATÃRIO
====================================
Para kits com 2, 3 ou 4 unidades, inclua obrigatoriamente:
- "kit 2 [produto]", "kit 3 [produto]", "kit 4 [produto]"
- "conjunto 2 [produto]", "conjunto 3 [produto]"
- "par de [produto]" quando kit 2 fizer sentido
- O produto unitÃ¡rio tambÃ©m (base para variaÃ§Ãµes de busca)

====================================
CATEGORIAS DE TERMOS - Gere para cada uma
====================================

TERMOS PRIMÃRIOS (alta intenÃ§Ã£o de compra)
- Como o comprador pesquisa o produto principal
- Marca + produto quando a marca for relevante para busca

TERMOS SECUNDÃRIOS (long tail, menor concorrÃªncia)
- Com atributos: material, capacidade, cor, tamanho
- CombinaÃ§Ãµes de 3-4 palavras

TERMOS DE KIT (se aplicÃ¡vel)
- "kit N [produto]", "conjunto N [produto]", "N unidades [produto]"

TERMOS ALTERNATIVOS (como o pÃºblico leigo pesquisa)
- SinÃ´nimos, nomes populares, variaÃ§Ãµes ortogrÃ¡ficas relevantes

====================================
REGRAS DE QUALIDADE
====================================
- Todos os termos em letras minÃºsculas
- Sem pontuaÃ§Ã£o ou sÃmbolos
- Sem termos genÃ©ricos vazios: "produto bom", "qualidade Ã³tima"
- Sem termos proibidos: preÃ§o, frete, garantia, promoÃ§Ã£o
- MÃ¡ximo 4 palavras por termo
- MÃnimo 12 termos, mÃ¡ximo 25

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
    # AGENTE 6 â COPYWRITER / DESCRIÃÃO (Groq, temperatura 0.0)
    #
    # MUDANÃA PRINCIPAL: O template de seÃ§Ãµes Ã© FIXO com marcadores exatos.
    # A IA preenche o conteÃºdo entre os marcadores â nunca altera os cabeÃ§alhos.
    # -------------------------------------------------------------------------
    PROMPT_COPY = """VocÃª Ã© um Copywriter Profissional de E-commerce especialista em conversÃ£o no Mercado Livre Brasil.
Sua tarefa Ã© criar uma descriÃ§Ã£o persuasiva, tÃ©cnica e otimizada para o produto fornecido.

====================================
ANÃLISE PRÃVIA OBRIGATÃRIA DO PRODUTO
====================================
Antes de escrever qualquer linha, analise o produto fornecido e extraia internamente:

A) PROBLEMA CENTRAL â qual dor, necessidade ou desejo o produto resolve?
B) PÃBLICO-ALVO â quem compra esse produto e em que situaÃ§Ã£o?
C) DIFERENCIAIS REAIS â o que torna esse produto diferente dos concorrentes genÃ©ricos?
D) CONDIÃÃES DE USO â onde, como e quando o produto Ã© utilizado?
E) OBJEÃÃES COMUNS â quais dÃºvidas ou medos o comprador tÃpico teria antes de comprar?

Essa anÃ¡lise deve guiar todo o texto. Nunca escreva de forma genÃ©rica.

====================================
TEMPLATE OBRIGATÃRIO (preencha EXATAMENTE nesta estrutura)
====================================
VocÃª DEVE retornar o texto usando EXATAMENTE os cabeÃ§alhos abaixo, na mesma ordem, sem alterar nenhum deles:

SOBRE O PRODUTO
[ParÃ¡grafo de abertura com gancho emocional ou situacional. Apresente o produto pelo nome completo e posicione-o como a soluÃ§Ã£o ideal.]

[ParÃ¡grafo de descriÃ§Ã£o tÃ©cnica humanizada. Explique como o produto funciona, conectando caracterÃsticas a benefÃcios reais.]

[ParÃ¡grafo de diferencial competitivo. Compare indiretamente com alternativas inferiores, sem citar concorrentes.]

[ParÃ¡grafo de contexto de uso expandido. Amplie os cenÃ¡rios de aplicaÃ§Ã£o alÃ©m do Ã³bvio.]

BENEFÃCIOS
- [Verbo de aÃ§Ã£o + resultado concreto]
- [Verbo de aÃ§Ã£o + resultado concreto]
- [Verbo de aÃ§Ã£o + resultado concreto]
- [Verbo de aÃ§Ã£o + resultado concreto]
- [Verbo de aÃ§Ã£o + resultado concreto]
- [Verbo de aÃ§Ã£o + resultado concreto]

DIFERENCIAIS DO PRODUTO
- [CaracterÃstica tÃ©cnica] â [por que isso importa para o comprador]
- [CaracterÃstica tÃ©cnica] â [por que isso importa para o comprador]
- [CaracterÃstica tÃ©cnica] â [por que isso importa para o comprador]
- [CaracterÃstica tÃ©cnica] â [por que isso importa para o comprador]
- [CaracterÃstica tÃ©cnica] â [por que isso importa para o comprador]

DÃVIDAS FREQUENTES
P: [Pergunta direta como o comprador faria]
R: [Resposta curta, confiante e que elimine a dÃºvida completamente]

P: [Pergunta direta como o comprador faria]
R: [Resposta curta, confiante e que elimine a dÃºvida completamente]

P: [Pergunta direta como o comprador faria]
R: [Resposta curta, confiante e que elimine a dÃºvida completamente]

O QUE VEM NO KIT
- [Produto 1] x [Quantidade]

IDEAL PARA
[ParÃ¡grafo Ãºnico de fechamento consolidando usos, pÃºblicos e situaÃ§Ãµes onde o produto se destaca. Finalize com afirmaÃ§Ã£o de autoridade ou qualidade que reforce a decisÃ£o de compra.]

====================================
DIRETRIZES DE ESTILO
====================================
- Tom: Profissional, confiÃ¡vel e prÃ³ximo.
- Proibido usar: "O melhor do mundo", "PreÃ§o imbatÃvel", "Entrega rÃ¡pida", "Qualidade incomparÃ¡vel", "NÃ£o perca", "Compre agora".
- Proibido usar adjetivos vazios sem comprovaÃ§Ã£o: "incrÃvel", "fantÃ¡stico", "revolucionÃ¡rio", "surpreendente".
- Cada seÃ§Ã£o deve parecer escrita especificamente para este produto. Texto genÃ©rico Ã© PROIBIDO.
- Use linguagem ativa e direta. Evite voz passiva.
- Bullet points curtos, diretos e escaneÃ¡veis em celular.
- NUNCA inclua nÃºmeros soltos de cÃ³digo de barras (EAN/SKU) no texto.
- Escreva de forma fluida (ex: "Kit com 4 pacotes de Feltro Preto" em vez de "4 unidades de Kit Feltro Pct C/40").
- MÃ¡ximo de 2800 caracteres no total.
- Os BENEFÃCIOS devem comeÃ§ar com verbo de aÃ§Ã£o no presente: Garante, Elimina, Protege, Facilita, Oferece...
- Os DIFERENCIAIS devem conectar caracterÃstica + benefÃcio com " â ".
- NUNCA altere, reordene ou renomeie os cabeÃ§alhos de seÃ§Ã£o.

Responda EXCLUSIVAMENTE em JSON, sem nenhum texto adicional ou explicaÃ§Ã£o:
{
  "descricao": "Texto completo da descriÃ§Ã£o com quebras de linha \\n"
}"""

    MAX_NOME = 60

    def __init__(self, gemini: Optional[GeminiClient] = None, groq: Optional[GroqClient] = None) -> None:
        self.gemini = gemini or GeminiClient()
        self.groq   = groq or GroqClient()

    # -------------------------------------------------------------------------
    # MÃ©todo principal
    # -------------------------------------------------------------------------

    def gerar_conteudo(
        self,
        nome_original: str,
        descricao_atual: str = "",
        features: Optional[Dict] = None,
        qtd_kit: int = 1,
        e_misto: bool = False,
        atributos_obrigatorios: Optional[List[str]] = None,
        gera_grade: bool = False,
    ) -> Dict:
        import json
        import re

        if not nome_original.strip():
            return {"titulo": "", "descricao": descricao_atual or "", "atributos": {}}

        # Limpa cÃ³digos EAN/SKU ou barras do inÃcio do nome
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
            raise ValueError("ServiÃ§o de Compliance indisponÃvel.")

        try:
            data_comp = json.loads(res_comp.strip())
            if data_comp.get("status") == "bloqueado":
                logger.warning(f"Compliance BLOQUEIO: {data_comp.get('motivo')}")
                raise ValueError(f"Produto violou politicas: {data_comp.get('motivo')}")
            if data_comp.get("status") == "revisar":
                logger.warning(f"Compliance REVISAR: {data_comp.get('motivo')}")
        except json.JSONDecodeError:
            logger.error(f"Erro JSON Compliance: {res_comp}")

        # --- AGENTE 2: TÃTULOS (Groq, temperatura 0.0) ---
        if gera_grade:
            titulo, titulos_grade = self._gerar_grade_titulos(contexto, nome_original)
        else:
            titulo, titulos_grade = self._gerar_titulo_unitario(contexto, nome_original)

        # --- AGENTE 6: DESCRIÃÃO (Gemini 2.5 Flash) ---
        logger.info("[Agente 3] Gerando DescriÃ§Ã£o Copywriter (Gemini 2.5 Flash)...")
        desc = self._gerar_descricao(titulo, contexto, descricao_atual)

        # --- AGENTE 4: FICHA TÃCNICA (Groq) ---
        logger.info("[Agente 4] Extraindo Ficha TÃ©cnica (Groq)...")
        atribs = self._gerar_ficha_tecnica(contexto, atributos_obrigatorios)

        # --- AGENTE 5: PALAVRAS-CHAVE (Groq) ---
        logger.info("[Agente 5] Identificando Palavras-chave SEO (Groq)...")
        keywords_data = self._gerar_keywords(titulo, contexto)

        # ValidaÃ§Ã£o de termos proibidos
        v_nome = TermosProibidos.verificar(titulo)
        if v_nome["bloqueado"]:
            raise ValueError(f"TÃtulo proibido: {v_nome['termo']}")

        # FormataÃ§Ã£o final do tÃtulo
        titulo = formatar_title_case(titulo)

        return {
            "titulo":        (titulo or nome_original)[:self.MAX_NOME],
            "titulos_grade": titulos_grade,
            "descricao":     desc or descricao_atual,
            "atributos":     atribs,
            "keywords":      keywords_data,
        }

    # -------------------------------------------------------------------------
    # SubmÃ©todos internos
    # -------------------------------------------------------------------------

    def _gerar_titulo_unitario(self, contexto: str, nome_original: str):
        """
        Gera um tÃtulo unitÃ¡rio via Gemini 2.5 Flash.
        A validaÃ§Ã£o de caracteres Ã© feita 100% em Python.
        """
        import json

        titulo = ""
        titulos_grade = {}
        max_tentativas = 3
        feedback_erro = ""

        for tentativa in range(max_tentativas):
            input_prompt = contexto
            if feedback_erro:
                input_prompt += f"\n\nATENÃÃO: A tentativa anterior gerou um tÃtulo com {feedback_erro}. Ajuste o comprimento e tente novamente."

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
                    logger.info(f"TÃtulo unitÃ¡rio aprovado ({tamanho} chars) na tentativa {tentativa + 1}: {titulo}")
                    break
                else:
                    feedback_erro = f"{tamanho} caracteres (precisa de 55 a 60)"
                    logger.warning(f"Tentativa {tentativa + 1}: {feedback_erro}")

                    # Na Ãºltima tentativa: aplica ajuste por Python
                    if tentativa == max_tentativas - 1:
                        titulo = ajustar_titulo(t_temp)
                        logger.warning(f"Fallback Python aplicado: {titulo!r} ({len(titulo)} chars)")

            except Exception as e:
                feedback_erro = f"JSON invÃ¡lido: {e}"
                logger.warning(f"Tentativa {tentativa + 1}: {feedback_erro}")

        if not titulo:
            titulo = ajustar_titulo(formatar_title_case(nome_original))

        return titulo, titulos_grade

    def _gerar_grade_titulos(self, contexto: str, nome_original: str):
        """
        Gera a grade de 8 tÃtulos via Gemini 2.5 Flash.
        A IA gera apenas a base otimizada. O Python mapeia e finaliza.
        """
        import json

        titulos_grade = {}
        max_tentativas = 3
        feedback_erro = ""

        for tentativa in range(max_tentativas):
            input_prompt = contexto
            if feedback_erro:
                input_prompt += f"\n\nATENÃÃO â Erros na tentativa anterior:\n{feedback_erro}\nCorrija no prÃ³ximo JSON."

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
                for chave_ia, chave_sistema in mapa_chaves.items():
                    texto_titulo = str(titulos_brutos.get(chave_ia, "")).strip()
                    titulos_temp[chave_sistema] = formatar_title_case(texto_titulo)

                # Verifica se todos os 8 tÃtulos foram gerados
                faltantes = [k for k, v in titulos_temp.items() if not v]
                if not faltantes:
                    titulos_grade = titulos_temp
                    logger.info(f"Grade aprovada na tentativa {tentativa + 1}. EstratÃ©gia SEO utilizada: {data_tit.get('estrategia_seo', 'NÃ£o informada')}")
                    break
                else:
                    feedback_erro = f"Faltaram os seguintes tÃtulos no JSON: {faltantes}"
                    logger.warning(f"Falha na tentativa {tentativa + 1}: {feedback_erro}")

            except Exception as e:
                feedback_erro = f"JSON invÃ¡lido: {e}"
                logger.warning(f"Tentativa {tentativa + 1}: {feedback_erro}")

        # Fallback de seguranÃ§a 100% Python
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
        Faz o parse robusto do JSON de descriÃ§Ã£o retornado pela IA.
        Trata chaves faltantes, quebras de linha literais, aspas nÃ£o escapadas e truncamento.
        """
        import json
        import re

        res_clean = res_copy.strip()

        # 1. Tentativa padrÃ£o com json.loads
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

        # 3. ExtraÃ§Ã£o via Regex (super robusta contra aspas/quebras/truncamento)
        # Remove tags markdown do bloco json se houver
        if res_clean.startswith("```"):
            res_clean = re.sub(r'^```(?:json)?\n', '', res_clean)
            res_clean = re.sub(r'\n```$', '', res_clean).strip()

        # Procura a chave "descricao" ou "descriÃ§ao" ou "descriÃ§Ã£o"
        match = re.search(r'"descri[cÃ§]ao"\s*:\s*"(.*)', res_clean, re.DOTALL)
        if match:
            content = match.group(1).strip()
            
            # Limpa fechamentos do JSON no fim do conteÃºdo se existirem
            if content.endswith('}'):
                content = content[:-1].strip()
            if content.endswith('"'):
                content = content[:-1]
                
            # Substitui escapes comuns
            content = content.replace('\\n', '\n')
            content = content.replace('\\"', '"')
            content = content.replace('\\\\', '\\')
            return content.strip()

        # 4. Fallback extremo: limpa o texto bruto se nÃ£o encontrar chaves
        fallback_text = res_clean
        if fallback_text.startswith('{') and fallback_text.endswith('}'):
            fallback_text = fallback_text[1:-1].strip()
        if fallback_text.startswith('"') and fallback_text.endswith('"'):
            fallback_text = fallback_text[1:-1].strip()
            
        fallback_text = fallback_text.replace('\\n', '\n').replace('\\"', '"')
        return fallback_text.strip()

    def _gerar_descricao(self, titulo: str, contexto: str, descricao_atual: str) -> str:
        """
        Gera descriÃ§Ã£o via Gemini 2.5 Flash.
        Valida presenÃ§a das 5 seÃ§Ãµes obrigatÃ³rias antes de aceitar.
        """
        import json

        max_tentativas = 3
        desc = ""
        feedback_erro = ""

        for tentativa in range(max_tentativas):
            input_prompt = f"TÃtulo Gerado: {titulo}\n{contexto}"
            if feedback_erro:
                input_prompt += f"\n\nATENÃÃO â Erros na tentativa anterior:\n{feedback_erro}\nCorrija no prÃ³ximo JSON."

            res_copy = self.gemini.chat(
                system_prompt=self.PROMPT_COPY,
                user_input=input_prompt,
                modelo="gemini-2.5-flash",
                use_cache=(tentativa == 0),
            )

            try:
                if not res_copy:
                    logger.warning(f"DescriÃ§Ã£o: resposta vazia na tentativa {tentativa + 1}.")
                    feedback_erro = "A IA retornou vazio. Responda com o JSON completo."
                    continue

                desc_temp = self._parse_descricao_json(res_copy)
                desc_temp = normalizar_descricao(desc_temp)

                if validar_descricao(desc_temp):
                    desc = desc_temp
                    logger.info(f"DescriÃ§Ã£o aprovada ({len(desc)} chars) na tentativa {tentativa + 1}.")
                    break
                else:
                    desc_norm = normalizar_texto_validador(desc_temp)
                    secoes_faltando = [s for s in SECOES_OBRIGATORIAS if SECOES_OBRIGATORIAS_NORM[s] not in desc_norm]
                    logger.warning(f"Tentativa {tentativa + 1}: seÃ§Ãµes ausentes: {secoes_faltando}")
                    feedback_erro = f"Faltaram as seÃ§Ãµes obrigatÃ³rias: {secoes_faltando}"
                    if tentativa == max_tentativas - 1:
                        # Aceita o melhor que tiver na Ãºltima tentativa
                        desc = desc_temp
                        logger.warning("Aceitando descriÃ§Ã£o parcial na Ãºltima tentativa.")

            except Exception as e:
                res_snippet = res_copy[:200] if res_copy else "None"
                logger.warning(f"Falha no JSON da DescriÃ§Ã£o (tentativa {tentativa + 1}): {e} | {res_snippet}")
                feedback_erro = f"O formato retornado nÃ£o foi um JSON vÃ¡lido ({e}). A resposta parecia cortada. Gere um JSON completo."

        return desc or descricao_atual

    def _gerar_ficha_tecnica(self, contexto: str, atributos_obrigatorios: Optional[List[str]]) -> Dict:
        """Gera atributos da ficha tÃ©cnica via Groq."""
        import json

        atribs = {}
        if not atributos_obrigatorios:
            return atribs

        input_tech = f"{contexto}\nCampos ObrigatÃ³rios: {', '.join(atributos_obrigatorios)}"
        res_tech = self.groq.chat(
            system_prompt=self.PROMPT_TECH,
            user_input=input_tech,
            modelo="llama-3.3-70b-versatile",
        )
        try:
            if res_tech:
                data_tech = json.loads(res_tech.strip())
                atribs = data_tech.get("atributos", {})
                logger.info(f"Atributos extraÃdos: {len(atribs)} campos.")
            else:
                logger.warning("Ficha TÃ©cnica: resposta vazia.")
        except Exception as e:
            res_snippet = res_tech[:200] if res_tech else "None"
            logger.warning(f"Falha no JSON da Ficha TÃ©cnica: {e} | {res_snippet}")

        return atribs

    def _gerar_keywords(self, titulo: str, contexto: str) -> Dict:
        """Gera palavras-chave SEO via Groq."""
        import json

        keywords_data = {}
        res_key = self.groq.chat(
            system_prompt=self.PROMPT_KEYWORDS,
            user_input=f"TÃtulo: {titulo}\n{contexto}",
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

    def _get_mapa_tags(self) -> Dict[str, int]:
        if self._mapa_tags_cache is None:
            self._mapa_tags_cache = self.client.obter_mapa_tags()
            if not self._mapa_tags_cache:
                logger.error("Mapa de tags retornado VAZIO do BaseLinker.")
            else:
                logger.info(f"Mapa de tags carregado: {list(self._mapa_tags_cache.keys())}")
        return self._mapa_tags_cache

    # ââ API BaseLinker ââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

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
        Baixa os dados do produto e os padroniza em um dicionÃ¡rio plano (flat),
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
            logger.error(f"Falha download/padronizaÃ§Ã£o PID={pid}: {exc}")
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

    # ââ PreÃ§o âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

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

    # ââ Textos ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

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
        return features

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

        conteudo = self.seo.gerar_conteudo(
            nome_original, descricao_original, features, e_misto=e_misto,
            atributos_obrigatorios=lista_obrigatorios_nomes,
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
                    features[n_limpo] = f"PREENCHER (OpÃ§Ãµes: {dica})"
                else:
                    features[n_limpo] = "PREENCHER"

        campos: Dict = {
            "name":        conteudo["titulo"],
            "description": conteudo["descricao"],
            "features":    features,
        }
        for chave, valor in tf.items():
            if isinstance(chave, str) and chave.startswith("extra_field_"):
                campos[chave] = valor
        campos["extra_field_39310"] = "un"
        
        # ForÃ§a os campos obrigatÃ³rios na Ficha TÃ©cnica (Features)
        if "features" in campos:
            features_ref = campos["features"]
            has_cond = False
            has_fmt = False
            for k in list(features_ref.keys()):
                k_lower = k.lower()
                if k_lower in ("condiÃ§Ã£o do item", "item_condition"):
                    features_ref[k] = "Novo"
                    has_cond = True
                if k_lower == "formato de venda":
                    # No caso isolado, sem quantidade, usamos Unidade como padrÃ£o se nao encontrar
                    features_ref[k] = "Unidade"
                    has_fmt = True
            
            if not has_cond:
                features_ref["CondiÃ§Ã£o do item"] = "Novo"
            if not has_fmt:
                features_ref["Formato de venda"] = "Unidade"

            # Limpa Modelo se for puramente numÃ©rico (SKU) ou usar placeholders proibidos
            for k in list(features_ref.keys()):
                if "modelo" in k.lower() or "model" in k.lower():
                    v = str(features_ref[k]).strip()
                    if not v or v.isdigit() or any(forbidden in v.lower() for forbidden in ["nÃ£o informado", "a informar", "desconhecido", "n/a", "null", "preencher", "nÃ£o se aplica", "nao se aplica"]):
                        del features_ref[k]

        return campos

    def _buscar_atributos_obrigatorios_ml(
        self, category_id_ml: str, features: Dict, dados: Dict
    ) -> List[str]:
        lista_obrigatorios_nomes = []
        termos_ignorar = [
            "motivo de gtin vazio", "reason_for_no_gtin", "motivo de gtin isento",
            "cÃ³digo universal", "ean", "gtin", "cÃ³digo de barras",
            "autorizaÃ§Ã£o sanitÃ¡ria", "aviso de operaÃ§Ã£o", "certificado da afe",
            "licenÃ§a do conama", "r.s/nso", "registro de producto do ms",
            "cofepris", "rpptm", "registro/nso", "comunicaÃ§Ã£o prÃ©via na anvisa"
        ]
        
        if category_id_ml and category_id_ml != "979589":
            try:
                logger.warning(f"[Ficha Tecnica] Verificando campos para Cat: {category_id_ml}")
                ml_client = MercadoLivreClient()
                obrigatorios_raw = ml_client.ficha_tecnica(category_id_ml)
                nomes_existentes = {k.lower() for k in features.keys()}

                for f in obrigatorios_raw:
                    n = f.get("name")
                    if not n: continue
                    
                    # Filtra termos de identificaÃ§Ã£o que o sistema jÃ¡ resolve ou que o usuÃ¡rio pediu para tirar
                    if any(t in n.lower() for t in termos_ignorar):
                        continue

                    allowed_values = f.get("values", [])
                    if n.lower() not in nomes_existentes:
                        valor_fisico = self._resolver_campo_fisico(n, dados)
                        if valor_fisico:
                            features[n] = valor_fisico
                        else:
                            campo_prompt = f"{n} [OBRIGATÃ“RIO]"
                            if allowed_values:
                                vals_str = ", ".join(allowed_values[:10])
                                campo_prompt = f"{n} [OBRIGATÃ“RIO] (Valores permitidos: {vals_str})"
                            lista_obrigatorios_nomes.append(campo_prompt)

                logger.info(f"[Ficha Tecnica] Pendentes para IA: {len(lista_obrigatorios_nomes)}")
            except Exception as exc:
                logger.error(f"[Ficha TÃ©cnica] Erro ao buscar dados do ML: {exc}")
        return lista_obrigatorios_nomes

    # â”€â”€ Payload â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
            logger.warning(f"Tag '{modalidade_tag}' nÃ£o encontrada no BaseLinker.")

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
            "height":          dados.get("height", 0),
            "width":           dados.get("width", 0),
            "length":          dados.get("length", 0),
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
        Retorna um GPC seguro e ativo baseado nos primeiros dÃgitos do NCM do produto.
        Caso nÃ£o mapeie, retorna None.
        """
        ncm_limpo = str(ncm).replace(".", "").strip()
        
        # Torneiras, registros, vÃ¡lvulas (NCM 8481...)
        if ncm_limpo.startswith("8481"):
            return "10002602"  # Torneiras / Misturadores
            
        # Cabos elÃ©tricos, fios, conectores (NCM 8544...)
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
    ) -> str:
        """
        ObtÃ©m ou gera um EAN para o kit.
        UnitÃ¡rio (qtd=1) -> sem novo EAN, exceto se forcar_unitario for True.
        Kit (qtd>1) -> Novo EAN registrado na GS1 Brasil.
        """
        if quantidade <= 1 and not forcar_unitario:
            return ""

        # Busca inteligente por quantidade no banco de dados local para compartilhamento de EAN
        gtin_existente = GtinKit.objects.filter(
            sku_origem=sku_origem,
            quantidade=quantidade
        ).exclude(gtin='PENDENTE').exclude(gtin='').first()

        if gtin_existente:
            logger.info(f"Reaproveitando EAN {gtin_existente.gtin} da variaÃ§Ã£o {gtin_existente.sku_kit} para a quantidade {quantidade}")
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

            # 1. Se foi enviado GPC manual e for vÃ¡lido (8 dÃgitos numÃ©ricos)
            if gpc_manual:
                gpc_manual_limpo = str(gpc_manual).strip()
                if gpc_manual_limpo.isdigit() and len(gpc_manual_limpo) == 8:
                    gpc = gpc_manual_limpo
                    logger.info(f"GPC manual fornecido pelo painel: {gpc}")
                else:
                    logger.warning(f"GPC manual fornecido '{gpc_manual}' Ã© invÃ¡lido (deve ter 8 dÃgitos numÃ©ricos).")

            # 2. Se nÃ£o foi fornecido GPC manual ou se ele Ã© invÃ¡lido, tenta obter do fallback estÃ¡tico de NCM
            if not gpc and ncm_limpo:
                logger.info("Buscando GPC no fallback estÃ¡tico por NCM...")
                gpc_fallback = self._obter_fallback_gpc_por_ncm(ncm_limpo)
                if gpc_fallback:
                    gpc = gpc_fallback
                    logger.info(f"GPC determinado via fallback estÃ¡tico de NCM: {gpc}")

            if not gpc:
                raise Exception("GPC nÃ£o pÃ´de ser determinado (nenhum GPC manual vÃ¡lido fornecido e nenhum fallback aplicÃ¡vel encontrado).")

            marca_real = dados_completos.get("marca")
            m_id = dados_completos.get("manufacturer_id")
            if m_id:
                mapa_marcas = self._get_mapa_fabricantes()
                marca_real = mapa_marcas.get(int(m_id)) or marca_real

            if not marca_real or "nÃ£o informada" in marca_real.lower():
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
            logger.error(f"Falha crÃtica no processo GS1: {e}")
            
            # Se for unitÃ¡rio (Qtd = 1), tenta usar o EAN original do produto base se for vÃ¡lido
            if quantidade == 1:
                ean_original = str(dados_completos.get("ean") or "").strip()
                # Verifica se Ã© um EAN brasileiro legÃtimo ou qualquer EAN vÃ¡lido (mÃnimo de 12 dÃgitos)
                if ean_original and len(ean_original) >= 12 and ean_original.lower() != "pendente":
                    logger.warning(f"GS1 falhou para unitÃ¡rio. Utilizando EAN original como fallback: {ean_original}")
                    obj.gtin = ean_original
                    obj.save()
                    return ean_original

            # Se nÃ£o puder usar o original ou se for kit (Qtd > 1):
            # Para evitar rejeiÃ§Ãµes duras nos marketplaces por EAN/GTIN invÃ¡lido (ex: iniciado com '1'),
            # salvamos como 'PENDENTE' no banco local e retornamos vazio ("") para que o produto seja publicado sem EAN,
            # permitindo que o anÃºncio vÃ¡ ao ar e o usuÃ¡rio possa corrigir o EAN posteriormente.
            logger.warning(f"GS1 falhou para kit/unitÃ¡rio (Qtd={quantidade}). Retornando vazio para evitar GTIN invÃ¡lido.")
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

        if "ean" in n or "cÃ³digo universal" in n:
            val = dados.get("ean")
            return str(val).strip() if val else None

        if "unidades por kit" in n or "unidades por embalagem" in n:
            return str(quantidade)

        if "altura" in n:
            val = dados.get("height")
            return f"{val} cm" if val and float(val) > 0 else None

        if "largura" in n:
            val = dados.get("width")
            return f"{val} cm" if val and float(val) > 0 else None

        if "comprimento" in n or "profundidade" in n:
            val = dados.get("length")
            return f"{val} cm" if val and float(val) > 0 else None

        if "peso" in n:
            val = dados.get("weight")
            if val and float(val) > 0:
                return f"{val} kg" if float(val) >= 1 else f"{float(val) * 1000:.0f} g"
            return None

        return None

    # â”€â”€ ExecuÃ§Ã£o â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

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
    ) -> Dict:
        """
        Cria a grade completa de 8 anÃºncios (1/2/3/4un x ClÃ¡ssico/Premium) no BaseLinker.
        Suporta atÃ© 4 SKUs secundÃ¡rios para Kit Misto.
        """
        logger.warning(f"Iniciando criaÃ§Ã£o de GRADE (8 anÃºncios) -> SKU Origem={sku_origem} | EAN UnitÃ¡rio={gerar_ean_unitario}")
        
        # ResoluÃ§Ã£o de categoria (Exige categoria informada manualmente, sem adivinhaÃ§Ã£o automÃ¡tica por tÃtulo)
        if not category_id_ml or category_id_ml == "979589":
            return {"sucesso": False, "msg": "Categoria do Mercado Livre nÃ£o informada. Por favor, forneÃ§a a Ã¡rvore de categoria no formulÃ¡rio do painel."}
            
        # Inicializa o cache local de execuÃ§Ã£o para compartilhar EANs entre ClÃ¡ssico e Premium
        self.gtins_gerados_lote: Dict[int, str] = {}

        # Registra o inÃcio de execuÃ§Ã£o do lote no cache para controle de cancelamento em tempo real
        from django.core.cache import cache
        cache.set(f"criacao_lote_{sku_origem}", "executando", timeout=120)

        dados = self._baixar_dados_produto(pid)
        if not dados:
            return {"sucesso": False, "msg": "Falha ao baixar dados do produto principal."}

        # Trava de SeguranÃ§a: ValidaÃ§Ã£o de dados essenciais
        erro_validacao = self._validar_dados_essenciais(dados)
        if erro_validacao:
            return {"sucesso": False, "msg": erro_validacao}

        if sku_origem == "30480":
            logger.warning(f"AUDITORIA PREÃ‡O SKU 30480: PreÃ§o Base={dados.get('price_brutto')} | Prices={dados.get('prices')}")

        # Consolida a lista de SKUs secundÃ¡rios
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
                return {"sucesso": False, "msg": f"SKU secundÃ¡rio {s_sec} nÃ£o encontrado no BaseLinker."}
            d_sec = self._baixar_dados_produto(pid_sec)
            if not d_sec:
                return {"sucesso": False, "msg": f"Falha ao baixar dados do SKU secundÃ¡rio {s_sec}."}
            erro_sec = self._validar_dados_essenciais(d_sec)
            if erro_sec:
                return {"sucesso": False, "msg": f"SecundÃ¡rio ({s_sec}): {erro_sec}"}
            pids_secundarios.append(pid_sec)
            dados_secundarios.append(d_sec)

        # Para retrocompatibilidade com referÃªncias a dados_secundario/pid_secundario
        dados_secundario = dados_secundarios[0] if dados_secundarios else None
        pid_secundario   = pids_secundarios[0] if pids_secundarios else None

        # ResoluÃ§Ã£o de categoria
        if not category_id_ml or category_id_ml == "979589":
            nome_base = (dados.get("text_fields", {}) or {}).get("name") or dados.get("name") or ""
            pred = MercadoLivreClient().predict_categoria(nome_base, limite=1)
            if pred:
                category_id_ml = str(pred[0]["category_id"])
                logger.info(f"Categoria prevista para grade: {category_id_ml}")

        category_id_bl = self.sincronizar_categoria_ml_para_baselinker(category_id_ml)

        # CÃ¡lculo da grade de preÃ§os
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
            return {"sucesso": False, "msg": f"Erro no motor de precificaÃ§Ã£o: {exc}"}

        if grade.bloqueado:
            return {"sucesso": False, "msg": f"Grade bloqueada: {grade.motivo_bloqueio}"}

        # Coleta atributos e gera conteÃºdo base (uma Ãºnica chamada Ã  IA)
        features_base     = self._extrair_features(dados)
        lista_obrigatorios = self._buscar_atributos_obrigatorios_ml(category_id_ml, features_base, dados)

        conteudo_base = self.seo.gerar_conteudo(
            dados.get("name", ""),
            dados.get("description", ""),
            features_base,
            e_misto=bool(dados_secundario),
            atributos_obrigatorios=lista_obrigatorios,
            gera_grade=True,
        )

        titulos_grade = conteudo_base.get("titulos_grade", {})
        desc_base     = conteudo_base.get("descricao", "")
        atribs_ia     = conteudo_base.get("atributos", {})
        features_base.update(atribs_ia)

        # Trava de SeguranÃ§a: Valida se a IA gerou tÃtulo e descriÃ§Ã£o vÃ¡lidos
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

        # Identifica se o produto base tem um EAN legÃtimo cadastrado no BaseLinker
        ean_original = str(dados.get("ean") or "").strip()
        ean_base_valido = bool(ean_original and ean_original.lower() != "pendente")
        precisa_gerar_unitario = gerar_ean_unitario or not ean_base_valido

        skus_criados = []
        ids_criados  = []

        for anuncio in grade.anuncios:
            if anuncio.bloqueado:
                logger.warning(f"Ignorando {anuncio.referencia}: Bloqueado.")
                continue

            # Verifica se o lote foi voluntariamente cancelado pelo usuÃ¡rio
            from django.core.cache import cache
            status_lote = cache.get(f"criacao_lote_{sku_origem}")
            if status_lote == "cancelado":
                logger.warning(f"Lote do SKU {sku_origem} foi voluntariamente CANCELADO pelo usuÃ¡rio durante o processamento.")
                # Executa o rollback completo de tudo que jÃ¡ foi criado atÃ© agora neste lote, inclusive na GS1
                self.executar_rollback_grade(sku_origem)
                return {
                    "sucesso": False,
                    "msg": f"CriaÃ§Ã£o interrompida com sucesso. Todos os anÃºncios criados atÃ© o momento para o SKU {sku_origem} foram removidos e os GTINs correspondentes foram inativados/excluÃdos na GS1."
                }

            sku_novo = self._obter_proximo_sku(None)
            if not sku_novo:
                break

            # Seleciona tÃtulo da grade para a variaÃ§Ã£o
            chave_grade  = f"{anuncio.quantidade}_{anuncio.modalidade}"
            titulo_base  = titulos_grade.get(chave_grade) or titulos_grade.get(str(anuncio.quantidade))

            if not titulo_base:
                titulo_base = conteudo_base.get('titulo', '')

            # Limpeza agressiva: Remove Kit, Unidade, UnitÃ¡rio etc. do tÃtulo base da IA
            termos_limpar = [
                "Kit", "Unidade", "Unidades", "UnitÃ¡rio", "Unitario", 
                "Premium", "ClÃ¡ssico", "Classico", "Original", "Pack"
            ]
            for termo in termos_limpar:
                # Regex para remover a palavra inteira isolada
                titulo_base = re.sub(rf'\b{termo}\b', '', titulo_base, flags=re.IGNORECASE)
            
            titulo_final = " ".join(titulo_base.split()).strip()

            # Garante prefixo "Kit N" para kits ou limpa para unitÃ¡rio
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
                logger.warning(f"Tag '{tag_name}' nÃ£o encontrada no BaseLinker.")

            # Se for um anÃºncio unitÃ¡rio clÃ¡ssico (Qtd = 1 e modalidade = classico), adiciona tambÃ©m a tag "Marketplace"
            if anuncio.quantidade == 1 and anuncio.modalidade == "classico":
                tag_marketplace = mapa_tags.get("marketplace")
                if tag_marketplace:
                    tags_enviar.append(tag_marketplace)
                else:
                    logger.warning("Tag 'Marketplace' nÃ£o encontrada no BaseLinker.")

            ean_final = ""
            if anuncio.quantidade == 1 and not precisa_gerar_unitario:
                ean_final = dados.get("ean", "")

            # Atualiza features por variaÃ§Ã£o
            features_var = features_base.copy()
            has_cond = False
            has_fmt = False
            
            for k in list(features_var.keys()):
                k_lower = k.lower()
                if ean_final and ("cÃ³digo universal" in k_lower or "ean" in k_lower):
                    features_var[k] = ean_final
                if "unidades por kit" in k_lower or "unidades por embalagem" in k_lower:
                    features_var[k] = str(anuncio.quantidade)
                if k_lower in ("condiÃ§Ã£o do item", "item_condition"):
                    features_var[k] = "Novo"
                    has_cond = True
                if k_lower == "formato de venda":
                    features_var[k] = "Unidade" if anuncio.quantidade == 1 else "Kit"
                    has_fmt = True

            if not has_cond:
                features_var["CondiÃ§Ã£o do item"] = "Novo"
            if not has_fmt:
                features_var["Formato de venda"] = "Unidade" if anuncio.quantidade == 1 else "Kit"

            # Garante que o Modelo nunca seja invÃ¡lido, vazio ou use placeholders proibidos
            has_modelo = False
            base_sku = str(dados.get("sku") or sku_origem).strip()
            for k in list(features_var.keys()):
                if "modelo" in k.lower() or "model" in k.lower():
                    has_modelo = True
                    v = str(features_var[k]).strip()
                    if not v or any(forbidden in v.lower() for forbidden in ["nÃ£o informado", "a informar", "desconhecido", "n/a", "null", "preencher", "nÃ£o se aplica", "nao se aplica"]):
                        features_var[k] = base_sku

            if not has_modelo:
                for n_p in lista_obrigatorios:
                    n_limpo = re.sub(r'\s*\(Valores permitidos:.*?\)', '', n_p).strip()
                    n_limpo = n_limpo.replace(" [OBRIGATÃRIO]", "").strip()
                    if "modelo" in n_limpo.lower() or "model" in n_limpo.lower():
                        features_var[n_limpo] = base_sku

            payload = {
                "inventory_id":    self.ID_INVENTARIO,
                "sku":             sku_novo,
                "ean":             ean_final,
                "manufacturer_id": dados.get("manufacturer_id") or 0,
                "text_fields":     {
                    **campos_base,
                    "name":        titulo_final,
                    "features":    features_var,
                    "description": desc_base,
                },
                "category_id":     category_id_bl,
                "price_brutto":    anuncio.preco_vitrine,
                "prices":          {"53516": anuncio.preco_vitrine},
                "weight":          (float(dados.get("weight") or 0) + sum(float(d.get("weight") or 0) for d in dados_secundarios)) * anuncio.quantidade,
                "height":          dados.get("height", 0),
                "width":           dados.get("width", 0),
                "length":          dados.get("length", 0),
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
                    # Verifica se o EAN para esta quantidade jÃ¡ foi gerado na execuÃ§Ã£o deste lote/grade
                    if anuncio.quantidade in self.gtins_gerados_lote:
                        ean_final = self.gtins_gerados_lote[anuncio.quantidade]
                        logger.info(f"Herdando EAN {ean_final} do cache de lote para SKU Kit {sku_novo} (Qtd {anuncio.quantidade})")
                        # Cria o registro individual da variaÃ§Ã£o no banco local apontando para o EAN herdado
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
                        )
                        if ean_final and ean_final != 'PENDENTE':
                            self.gtins_gerados_lote[anuncio.quantidade] = ean_final

                if ean_final and (anuncio.quantidade > 1 or precisa_gerar_unitario):
                    for k in list(features_var.keys()):
                        if "cÃ³digo universal" in k.lower() or "ean" in k.lower():
                            features_var[k] = ean_final

                    update_payload = {
                        "inventory_id": self.ID_INVENTARIO,
                        "product_id":   int(id_kit),
                        "ean":          ean_final,
                        "text_fields":  {
                            **payload["text_fields"],
                            "features": features_var,
                        },
                    }
                    self.client._request("addInventoryProduct", update_payload)
                    logger.info(f"Produto {sku_novo} atualizado com EAN {ean_final} e Ficha TÃ©cnica.")

            except Exception as exc:
                logger.error(f"Erro ao criar variaÃ§Ã£o {anuncio.referencia} (SKU {sku_novo}): {exc}")
                if "Falha ao obter ID" in str(exc) or "API Baselinker falhou" in str(exc):
                    self._reciclar_numero(sku_novo)

        return {
            "sucesso": True,
            "skus":    skus_criados,
            "ids":     ids_criados,
            "msg":     f"Grade criada com {len(skus_criados)} anÃºncios no BaseLinker.",
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
                "erro_detalhe": "Produto nÃ£o encontrado no BaseLinker.",
            }

        # Trava de SeguranÃ§a: ValidaÃ§Ã£o de dados essenciais
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
                    "msg": f"SKU secundÃ¡rio {sku_secundario} nÃ£o encontrado.",
                    "erro_detalhe": f"SKU {sku_secundario} nÃ£o encontrado no BaseLinker.",
                }
            dados_secundario = self._baixar_dados_produto(pid_secundario)
            if not dados_secundario:
                return {
                    "sucesso": False,
                    "msg": f"Falha ao baixar dados do SKU secundÃ¡rio {sku_secundario}.",
                    "erro_detalhe": "Produto secundÃ¡rio nÃ£o encontrado.",
                }
            
            # ValidaÃ§Ã£o para produto secundÃ¡rio
            erro_validacao_sec = self._validar_dados_essenciais(dados_secundario)
            if erro_validacao_sec:
                return {"sucesso": False, "msg": f"SecundÃ¡rio: {erro_validacao_sec}"}

        category_id_bl = self.sincronizar_categoria_ml_para_baselinker(category_id_ml)

        # Gera conteÃºdo via IA antes de alocar SKU
        try:
            tf = dados.get("text_fields", {}) or {}
            nome_original_prod = tf.get("name") or dados.get("name") or ""
            descricao_original = tf.get("description") or dados.get("description") or ""
            features_base      = self._extrair_features(dados)
            lista_obrigatorios = self._buscar_atributos_obrigatorios_ml(category_id_ml, features_base, dados)

            conteudo_ia = self.seo.gerar_conteudo(
                nome_original_prod,
                descricao_original,
                features_base,
                e_misto=bool(dados_secundario),
                atributos_obrigatorios=lista_obrigatorios,
                gera_grade=False,
            )
        except Exception as exc:
            return {"sucesso": False, "msg": f"Erro crÃtico na geraÃ§Ã£o de conteÃºdo pela IA: {exc}"}

        # Trava de SeguranÃ§a: Valida se a IA gerou tÃtulo e descriÃ§Ã£o vÃ¡lidos
        erro_conteudo = self._validar_conteudo_ia(conteudo_ia, nome_original_prod)
        if erro_conteudo:
            return {"sucesso": False, "msg": erro_conteudo}

        for tentativa in range(1, 6):
            sku_final = self._obter_proximo_sku(sku_desejado)
            if not sku_final:
                return {
                    "sucesso": False,
                    "msg": "SKU indisponÃvel.",
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

        return {"sucesso": False, "msg": "Falha apÃ³s 5 tentativas.", "erro_detalhe": "Verifique o BaseLinker."}

    # ââ UtilitÃ¡rios âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

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
                        return parent_id_atual if parent_id_atual > 0 else self.ID_CATEGORIA_KITS
                    cat_id = novo_id
                    mapa_bl[chave] = cat_id

                parent_id_atual = cat_id

            return parent_id_atual if parent_id_atual > 0 else self.ID_CATEGORIA_KITS

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

    def _obter_proximo_sku(self, sku_desejado: Optional[str]) -> Optional[str]:
        if sku_desejado:
            return str(sku_desejado)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT get_next_kit_sku()")
                row = cursor.fetchone()
                return str(row[0]) if row and row[0] is not None else None
        except Exception as exc:
            logger.error(f"DB get_next_kit_sku: {exc}")
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
        if not sku or not sku.isdigit():
            return
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO criacao_estruturado_numeroreciclado (numero) VALUES (%s) ON CONFLICT DO NOTHING",
                    [int(sku)],
                )
        except Exception as exc:
            logger.warning(f"Reciclar {sku}: {exc}")

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
                "msg": f"Nenhum kit no histÃ³rico encontrado para o SKU de origem {sku_origem}."
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
                logger.warning(f"Chamando inativaÃ§Ã£o GS1 para o GTIN {g} devido ao Rollback.")
                gs1.inativar_ou_excluir_produto(g)
            except Exception as e:
                logger.error(f"Erro ao inativar GTIN {g} na GS1 durante rollback: {e}")

        # Deleta registros de histÃ³rico
        historicos.delete()

        # Deleta registros de GTIN local (GtinKit)
        gtins_deletados, _ = GtinKit.objects.filter(sku_origem=sku_origem).delete()

        msg = f"Rollback concluÃdo. Deletados {deletados_bl} anÃºncios no BaseLinker e {gtins_deletados} GTINs locais limpos. SKUs reciclados: {', '.join(skus_reciclados)}."
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
        Deleta um Ãºnico produto do BaseLinker, remove do histÃ³rico local,
        limpa o GTIN associado a esse SKU de kit e recicla o SKU.
        """
        logger.warning(f"Iniciando exclusÃ£o individual -> SKU Kit={sku_kit} | ID Kit={id_kit}")
        
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
                logger.warning(f"Chamando inativaÃ§Ã£o GS1 para o GTIN {g} devido Ã  exclusÃ£o individual.")
                gs1.inativar_ou_excluir_produto(g)
            except Exception as e:
                logger.error(f"Erro ao inativar GTIN {g} na GS1 durante exclusÃ£o: {e}")

        # Recicla o SKU
        self._reciclar_numero(sku_kit)

        # Deleta do histÃ³rico local
        HistoricoKit.objects.filter(id_kit=id_kit).delete()

        # Deleta do banco de GTINs local (GtinKit)
        GtinKit.objects.filter(sku_kit=sku_kit).delete()

        if sucesso_bl:
            msg = f"Produto {sku_kit} excluÃdo com sucesso do BaseLinker e GTIN local removido."
            return {"sucesso": True, "msg": msg}
        else:
            msg = f"GTIN local e histÃ³rico de {sku_kit} limpos, mas falhou ao excluir no BaseLinker: {erro_bl}."
            return {"sucesso": True, "msg": msg}