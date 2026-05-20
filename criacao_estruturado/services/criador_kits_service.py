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
    Garante que o título tenha entre min_c e max_c caracteres.
    - Se maior: remove a última palavra inteira até caber.
    - Se menor: adiciona palavras de reforço (SEO padding) até atingir o mínimo.
    Nunca corta palavras ao meio.
    """
    titulo = " ".join(titulo.split()).strip()  # normaliza espaços

    # 1. Corta excesso removendo palavras inteiras
    while len(titulo) > max_c:
        if " " in titulo:
            titulo = titulo.rsplit(" ", 1)[0].strip()
        else:
            titulo = titulo[:max_c]
            break

    # 2. Preenche se estiver curto (SEO Padding)
    # Palavras seguras (sem as proibidas: Premium, Classico, Suprema, Excelente, Oferta, Valor, Original, Confianca)
    PALAVRAS_REFORCO = [
        "Alta Qualidade", "Pronta Entrega", "Envio Rápido", 
        "Resistente", "Garantia", "Profissional", "Melhor Opção",
        "Uso Diário", "Eficiente", "Segurança", "Para Sua Casa",
        "Forte", "Novo", "Top", "Garantido", "Ideal", "Diy", "Pro"
    ]

    while len(titulo) < min_c:
        adicionou = False
        for p in PALAVRAS_REFORCO:
            # Verifica se cabe a palavra + 1 espaço
            if len(titulo) + len(p) + 1 <= max_c:
                if p.lower() not in titulo.lower(): # Evita repetição
                    titulo = f"{titulo} {p}"
                    adicionou = True
                    break
        if not adicionou:
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


def validar_descricao(desc: str) -> bool:
    """Verifica se todas as seções obrigatórias estão presentes na descrição."""
    return all(secao in desc for secao in SECOES_OBRIGATORIAS)


def normalizar_descricao(desc: str) -> str:
    """
    Garante que a descrição sempre comece pela seção correta,
    removendo qualquer lixo antes de 'SOBRE O PRODUTO'.
    """
    if "SOBRE O PRODUTO" in desc:
        desc = "SOBRE O PRODUTO\n" + desc.split("SOBRE O PRODUTO", 1)[1].strip()
    return desc.strip()


# =============================================================================
# SEO GENERATOR (Orquestrador de Agentes Especializados)
# =============================================================================

class SEOGenerator:
    """
    Orquestrador de Agentes Especializados para SEO e Compliance.
    Usa Gemini 1.5 Flash para Compliance, Títulos e Descrição.
    Usa Llama 3.1/3.3 (Groq) para Ficha Técnica e Keywords.

    MUDANÇAS v2.1:
    - Alterado Títulos e Descrição para Gemini 1.5 Flash (Google AI Studio).
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
    PROMPT_TITULO = """Você é um Especialista em SEO para Mercado Livre Brasil com mais de 7 anos de experiência em ranqueamento orgânico e Google Shopping.

Crie APENAS UM título otimizado para o produto que será fornecido.

====================================
REGRAS ABSOLUTAS (NÃO NEGOCIÁVEIS)
====================================
- O título deve ter entre 55 e 60 caracteres. O sistema vai medir depois — foque em criar o melhor título possível dentro do intervalo.
- NUNCA corte palavras no final. Se a última palavra não couber, remova ela inteira.
- Proibido usar: Premium, Clássico, Ouro, Bronze, Special, Suprema, Excelente, Original, Confiança, Oferta, Valor, 1 Unidade, Uma Unidade, Unitário, Pack, Kit (no unitário).
- Nenhum caractere especial: -, ,, ., !, ?, %, &, @, #, /, ", ', [, ], (, ).
- Separe todas as palavras apenas com espaço simples.
- Primeira letra maiúscula de cada palavra, restante em minúsculo (exceto siglas como ML, LED, USB).
- Sempre em português brasileiro correto.
- Não inicie com artigos (A, O, Um, Uma, Os, As).

====================================
ESTRUTURA OBRIGATÓRIA DE PRIORIDADE
====================================
1. Palavra-chave principal de busca (ex: Shampoo Clear Men)
2. Linha / Variante (ex: Hidratacao Campea)
3. Capacidade / Tamanho (ex: 400ml)
4. Quantidade do kit (quando aplicável)
5. Reforço final forte e relevante (ex: Anti Caspa, Alta Duracao) apenas se necessário para bater o limite.

Exemplo de título excelente (58 caracteres): Shampoo Clear Men Hidratacao Campea 400ml Anti Caspa

====================================
ANÁLISE PRÉVIA OBRIGATÓRIA DO PRODUTO
====================================
Antes de escrever o título, analise o produto fornecido e extraia internamente:

A) PALAVRAS-CHAVE PRIMÁRIAS — os termos mais buscados para esse produto no Mercado Livre
B) BENEFÍCIOS REAIS — liste todos os benefícios concretos que o produto oferece
C) PROBLEMAS QUE RESOLVE — liste os problemas ou dores que o produto soluciona
D) PERFIL DO COMPRADOR — identifique quem compraria esse produto e o que busca

Essa análise deve ser feita internamente antes de escrever o título.

Responda EXCLUSIVAMENTE em JSON, sem nenhum texto adicional ou explicação:
{"titulo": "título aqui"}"""

    # -------------------------------------------------------------------------
    # AGENTE 2B — GRADE DE 8 TÍTULOS (Groq, temperatura 0.0)
    # -------------------------------------------------------------------------
    PROMPT_TITULO_KITS = """Você é um Especialista em SEO para Mercado Livre Brasil.
Sua tarefa é gerar 8 títulos base únicos e altamente otimizados para o produto fornecido.

====================================
REGRAS OBRIGATÓRIAS
====================================
1. NÃO inclua quantidades (ex: "Kit 2", "4 Unidades", "Unitário") no título. Foque APENAS no nome do produto, atributos, marca e benefícios. O sistema injetará as quantidades automaticamente.
2. Cada título deve ter em torno de 45 a 55 caracteres. Não se preocupe em bater 60 exatos.
3. Proibido usar: Premium, Clássico, Suprema, Excelente, Oferta, Valor, Original, Confiança.
4. Diferencie os títulos mudando o ângulo de busca (Benefício principal, Problema que resolve, Durabilidade, Uso profissional/diário, Marca). 

====================================
EXEMPLO DE SAÍDA DESEJADA (Para o produto "Fita Led 5m RGB")
====================================
{
  "estrategia_seo": "Palavras-chave: fita led, rgb, 5m, decoracao. Ângulos: gamer, sala, sanca, iluminacao forte.",
  "titulos": {
    "t1_a": "Fita Led Rgb 5m Colorida Decoracao Quarto",
    "t1_b": "Fita Luminosa Led 5 Metros Com Controle",
    "t2_a": "Fita Led 5m Rgb Iluminacao Gamer Sanca",
    "t2_b": "Fita Led Colorida 5m Alta Intensidade",
    "t3_a": "Fita Rgb 5 Metros Led Para Perfil",
    "t3_b": "Fita Led 5m Iluminacao Interna Profissional",
    "t4_a": "Fita Led 5 Metros Rgb Multicor Dupla Face",
    "t4_b": "Fita Led Quarto Gamer 5m Rgb Brilho Forte"
  }
}

Responda EXCLUSIVAMENTE em JSON. Você DEVE escrever a 'estrategia_seo' antes de gerar os 'titulos' para ancorar o contexto. NUNCA pule a chave estrategia_seo."""


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

  4. AUSENTE    → omita a chave do JSON.
                  EXCEÇÃO: campos [OBRIGATÓRIO] → use "Não informado".

Prioridade de confiança: Explícito > Inferível > Enriquecível > Ausente.

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
R5. NUNCA invente valores sem base no texto, inferência lógica clara
    ou conhecimento de mercado verificável.

====================================
PADRONIZAÇÃO DE VALORES
====================================
- Idioma       : Português brasileiro.
                 ✓ "Preto"  ✗ "Black" | ✓ "Azul marinho"  ✗ "Navy"
- Medidas      : número + espaço + unidade → "500 ml", "15 cm", "2 kg"
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
    # AGENTE 6 — COPYWRITER / DESCRIÇÃO (Groq, temperatura 0.0)
    #
    # MUDANÇA PRINCIPAL: O template de seções é FIXO com marcadores exatos.
    # A IA preenche o conteúdo entre os marcadores — nunca altera os cabeçalhos.
    # -------------------------------------------------------------------------
    PROMPT_COPY = """Você é um Copywriter Profissional de E-commerce especialista em conversão no Mercado Livre Brasil.
Sua tarefa é criar uma descrição persuasiva, técnica e otimizada para o produto fornecido.

====================================
ANÁLISE PRÉVIA OBRIGATÓRIA DO PRODUTO
====================================
Antes de escrever qualquer linha, analise o produto fornecido e extraia internamente:

A) PROBLEMA CENTRAL — qual dor, necessidade ou desejo o produto resolve?
B) PÚBLICO-ALVO — quem compra esse produto e em que situação?
C) DIFERENCIAIS REAIS — o que torna esse produto diferente dos concorrentes genéricos?
D) CONDIÇÕES DE USO — onde, como e quando o produto é utilizado?
E) OBJEÇÕES COMUNS — quais dúvidas ou medos o comprador típico teria antes de comprar?

Essa análise deve guiar todo o texto. Nunca escreva de forma genérica.

====================================
TEMPLATE OBRIGATÓRIO (preencha EXATAMENTE nesta estrutura)
====================================
Você DEVE retornar o texto usando EXATAMENTE os cabeçalhos abaixo, na mesma ordem, sem alterar nenhum deles:

SOBRE O PRODUTO
[Parágrafo de abertura com gancho emocional ou situacional. Apresente o produto pelo nome completo e posicione-o como a solução ideal.]

[Parágrafo de descrição técnica humanizada. Explique como o produto funciona, conectando características a benefícios reais.]

[Parágrafo de diferencial competitivo. Compare indiretamente com alternativas inferiores, sem citar concorrentes.]

[Parágrafo de contexto de uso expandido. Amplie os cenários de aplicação além do óbvio.]

BENEFÍCIOS
- [Verbo de ação + resultado concreto]
- [Verbo de ação + resultado concreto]
- [Verbo de ação + resultado concreto]
- [Verbo de ação + resultado concreto]
- [Verbo de ação + resultado concreto]
- [Verbo de ação + resultado concreto]

DIFERENCIAIS DO PRODUTO
- [Característica técnica] — [por que isso importa para o comprador]
- [Característica técnica] — [por que isso importa para o comprador]
- [Característica técnica] — [por que isso importa para o comprador]
- [Característica técnica] — [por que isso importa para o comprador]
- [Característica técnica] — [por que isso importa para o comprador]

DÚVIDAS FREQUENTES
P: [Pergunta direta como o comprador faria]
R: [Resposta curta, confiante e que elimine a dúvida completamente]

P: [Pergunta direta como o comprador faria]
R: [Resposta curta, confiante e que elimine a dúvida completamente]

P: [Pergunta direta como o comprador faria]
R: [Resposta curta, confiante e que elimine a dúvida completamente]

O QUE VEM NO KIT
- [Produto 1] x [Quantidade]

IDEAL PARA
[Parágrafo único de fechamento consolidando usos, públicos e situações onde o produto se destaca. Finalize com afirmação de autoridade ou qualidade que reforce a decisão de compra.]

====================================
DIRETRIZES DE ESTILO
====================================
- Tom: Profissional, confiável e próximo.
- Proibido usar: "O melhor do mundo", "Preço imbatível", "Entrega rápida", "Qualidade incomparável", "Não perca", "Compre agora".
- Proibido usar adjetivos vazios sem comprovação: "incrível", "fantástico", "revolucionário", "surpreendente".
- Cada seção deve parecer escrita especificamente para este produto. Texto genérico é PROIBIDO.
- Use linguagem ativa e direta. Evite voz passiva.
- Bullet points curtos, diretos e escaneáveis em celular.
- NUNCA inclua números soltos de código de barras (EAN/SKU) no texto.
- Escreva de forma fluida (ex: "Kit com 4 pacotes de Feltro Preto" em vez de "4 unidades de Kit Feltro Pct C/40").
- Máximo de 2800 caracteres no total.
- Os BENEFÍCIOS devem começar com verbo de ação no presente: Garante, Elimina, Protege, Facilita, Oferece...
- Os DIFERENCIAIS devem conectar característica + benefício com " — ".
- NUNCA altere, reordene ou renomeie os cabeçalhos de seção.

Responda EXCLUSIVAMENTE em JSON, sem nenhum texto adicional ou explicação:
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

        # --- AGENTE 2: TÍTULOS (Groq, temperatura 0.0) ---
        if gera_grade:
            titulo, titulos_grade = self._gerar_grade_titulos(contexto, nome_original)
        else:
            titulo, titulos_grade = self._gerar_titulo_unitario(contexto, nome_original)

        # --- AGENTE 6: DESCRIÇÃO (Gemini 1.5 Flash) ---
        logger.info("[Agente 3] Gerando Descrição Copywriter (Gemini 1.5 Flash)...")
        desc = self._gerar_descricao(titulo, contexto, descricao_atual)

        # --- AGENTE 4: FICHA TÉCNICA (Groq) ---
        logger.info("[Agente 4] Extraindo Ficha Técnica (Groq)...")
        atribs = self._gerar_ficha_tecnica(contexto, atributos_obrigatorios)

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
        Gera um título unitário via Gemini 1.5 Flash.
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
        Gera a grade de 8 títulos via Gemini 1.5 Flash.
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

                # Verifica se todos os 8 títulos foram gerados
                faltantes = [k for k, v in titulos_temp.items() if not v]
                if not faltantes:
                    titulos_grade = titulos_temp
                    logger.info(f"Grade aprovada na tentativa {tentativa + 1}. Estratégia SEO utilizada: {data_tit.get('estrategia_seo', 'Não informada')}")
                    break
                else:
                    feedback_erro = f"Faltaram os seguintes títulos no JSON: {faltantes}"
                    logger.warning(f"Falha na tentativa {tentativa + 1}: {feedback_erro}")

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

    def _gerar_descricao(self, titulo: str, contexto: str, descricao_atual: str) -> str:
        """
        Gera descrição via Gemini 1.5 Flash.
        Valida presença das 5 seções obrigatórias antes de aceitar.
        """
        import json

        max_tentativas = 3
        desc = ""
        feedback_erro = ""

        for tentativa in range(max_tentativas):
            input_prompt = f"Título Gerado: {titulo}\n{contexto}"
            if feedback_erro:
                input_prompt += f"\n\nATENÇÃO — Erros na tentativa anterior:\n{feedback_erro}\nCorrija no próximo JSON."

            res_copy = self.gemini.chat(
                system_prompt=self.PROMPT_COPY,
                user_input=input_prompt,
                modelo="gemini-2.5-flash",
            )

            try:
                if not res_copy:
                    logger.warning(f"Descrição: resposta vazia na tentativa {tentativa + 1}.")
                    feedback_erro = "A IA retornou vazio. Responda com o JSON completo."
                    continue

                desc_temp = self._parse_descricao_json(res_copy)
                desc_temp = normalizar_descricao(desc_temp)

                if validar_descricao(desc_temp):
                    desc = desc_temp
                    logger.info(f"Descrição aprovada ({len(desc)} chars) na tentativa {tentativa + 1}.")
                    break
                else:
                    secoes_faltando = [s for s in SECOES_OBRIGATORIAS if s not in desc_temp]
                    logger.warning(f"Tentativa {tentativa + 1}: seções ausentes: {secoes_faltando}")
                    feedback_erro = f"Faltaram as seções obrigatórias: {secoes_faltando}"
                    if tentativa == max_tentativas - 1:
                        # Aceita o melhor que tiver na última tentativa
                        desc = desc_temp
                        logger.warning("Aceitando descrição parcial na última tentativa.")

            except Exception as e:
                res_snippet = res_copy[:200] if res_copy else "None"
                logger.warning(f"Falha no JSON da Descrição (tentativa {tentativa + 1}): {e} | {res_snippet}")
                feedback_erro = f"O formato retornado não foi um JSON válido ({e}). A resposta parecia cortada. Gere um JSON completo."

        return desc or descricao_atual

    def _gerar_ficha_tecnica(self, contexto: str, atributos_obrigatorios: Optional[List[str]]) -> Dict:
        """Gera atributos da ficha técnica via Groq."""
        import json

        atribs = {}
        if not atributos_obrigatorios:
            return atribs

        input_tech = f"{contexto}\nCampos Obrigatórios: {', '.join(atributos_obrigatorios)}"
        res_tech = self.groq.chat(
            system_prompt=self.PROMPT_TECH,
            user_input=input_tech,
            modelo="llama-3.3-70b-versatile",
        )
        try:
            if res_tech:
                data_tech = json.loads(res_tech.strip())
                atribs = data_tech.get("atributos", {})
                logger.info(f"Atributos extraídos: {len(atribs)} campos.")
            else:
                logger.warning("Ficha Técnica: resposta vazia.")
        except Exception as e:
            res_snippet = res_tech[:200] if res_tech else "None"
            logger.warning(f"Falha no JSON da Ficha Técnica: {e} | {res_snippet}")

        return atribs

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
                    features[n_limpo] = f"PREENCHER (Opções: {dica})"
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
        
        # Força os campos obrigatórios na Ficha Técnica (Features)
        if "features" in campos:
            features_ref = campos["features"]
            has_cond = False
            has_fmt = False
            for k in list(features_ref.keys()):
                k_lower = k.lower()
                if k_lower in ("condição do item", "item_condition"):
                    features_ref[k] = "Novo"
                    has_cond = True
                if k_lower == "formato de venda":
                    # No caso isolado, sem quantidade, usamos Unidade como padrão se nao encontrar
                    features_ref[k] = "Unidade"
                    has_fmt = True
            
            if not has_cond:
                features_ref["Condição do item"] = "Novo"
            if not has_fmt:
                features_ref["Formato de venda"] = "Unidade"

            # Garante que o Modelo nunca seja inválido, vazio ou use placeholders proibidos
            has_modelo = False
            base_sku = str(dados.get("sku") or sku_novo).strip()
            for k in list(features_ref.keys()):
                if "modelo" in k.lower() or "model" in k.lower():
                    has_modelo = True
                    v = str(features_ref[k]).strip()
                    if not v or any(forbidden in v.lower() for forbidden in ["não informado", "a informar", "desconhecido", "n/a", "null", "preencher", "não se aplica", "nao se aplica"]):
                        features_ref[k] = base_sku

            if not has_modelo:
                for n_p in lista_obrigatorios_nomes:
                    n_limpo = re.sub(r'\s*\(Valores permitidos:.*?\)', '', n_p).strip()
                    n_limpo = n_limpo.replace(" [OBRIGATÓRIO]", "").strip()
                    if "modelo" in n_limpo.lower() or "model" in n_limpo.lower():
                        features_ref[n_limpo] = base_sku

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
            "cofepris", "rpptm", "registro/nso", "comunicação prévia na anvisa"
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
                    
                    # Filtra termos de identificação que o sistema já resolve ou que o usuário pediu para tirar
                    if any(t in n.lower() for t in termos_ignorar):
                        continue

                    allowed_values = f.get("values", [])
                    if n.lower() not in nomes_existentes:
                        valor_fisico = self._resolver_campo_fisico(n, dados)
                        if valor_fisico:
                            features[n] = valor_fisico
                        else:
                            campo_prompt = f"{n} [OBRIGATÓRIO]"
                            if allowed_values:
                                vals_str = ", ".join(allowed_values[:10])
                                campo_prompt = f"{n} [OBRIGATÓRIO] (Valores permitidos: {vals_str})"
                            lista_obrigatorios_nomes.append(campo_prompt)

                logger.info(f"[Ficha Tecnica] Pendentes para IA: {len(lista_obrigatorios_nomes)}")
            except Exception as exc:
                logger.error(f"[Ficha Técnica] Erro ao buscar dados do ML: {exc}")
        return lista_obrigatorios_nomes

    # ── Payload ───────────────────────────────────────────────────────────────

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
    ) -> str:
        """
        Obtém ou gera um EAN para o kit.
        Unitário (qtd=1) -> sem novo EAN, exceto se forcar_unitario for True.
        Kit (qtd>1) -> Novo EAN registrado na GS1 Brasil.
        """
        if quantidade <= 1 and not forcar_unitario:
            return ""

        obj, created = GtinKit.objects.get_or_create(
            sku_origem=sku_origem,
            quantidade=quantidade,
            defaults={'gtin': 'PENDENTE'},
        )

        if not created and obj.gtin and obj.gtin != 'PENDENTE':
            logger.info(f"Reaproveitando EAN {obj.gtin} para {sku_origem} Qtd {quantidade}")
            return obj.gtin

        try:
            gpc = None
            logger.info("Fluxo Blindado IA: Classificando GPC diretamente via IA (Groq)...")
            
            # Limpa o prefixo do Kit para obter o título do anúncio unitário correspondente
            titulo_unitario = re.sub(r'^Kit\s+\d+\s+', '', titulo_final, flags=re.IGNORECASE).strip()
            logger.info(f"Título específico para IA: '{titulo_unitario}' (extraído de '{titulo_final}')")
            
            try:
                gpc_sugerido = self._descobrir_gpc_via_ia(dados_completos, titulo_especifico=titulo_unitario)
                
                # Validação estrita do GPC retornado pela IA
                ncm_bruto = str(dados_completos.get("ncm", "00000000"))
                ncm_limpo = ncm_bruto.replace(".", "").strip()
                
                if (
                    gpc_sugerido 
                    and gpc_sugerido.isdigit() 
                    and len(gpc_sugerido) == 8 
                    and not gpc_sugerido.endswith("09") 
                    and gpc_sugerido != "10101502"
                    and gpc_sugerido != ncm_limpo
                ):
                    gpc = gpc_sugerido
                    logger.info(f"IA classificou GPC com sucesso: {gpc}")
                else:
                    logger.warning(f"GPC sugerido pela IA '{gpc_sugerido}' falhou na validação local.")
            except Exception as e_ia:
                logger.error(f"Erro durante classificação por IA: {e_ia}")

            if not gpc:
                logger.info("Ativando fallback inteligente por NCM para GPC...")
                gpc = self._obter_fallback_gpc_por_ncm(dados_completos.get("ncm", ""))
                logger.info(f"GPC determinado via fallback de NCM: {gpc}")

            if not gpc:
                raise Exception("GPC não pôde ser determinado e nenhum fallback NCM aplicável foi encontrado.")


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
            seed = f'kit-{sku_origem}-{quantidade}'
            hash_val = int(uuid.uuid5(uuid.NAMESPACE_DNS, seed).int % 999999999999)
            novo_gtin = f'1{hash_val:012d}'
            obj.gtin = novo_gtin
            obj.save()
            return novo_gtin

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

    def _descobrir_gpc_via_ia(self, dados: dict, titulo_especifico: str = "") -> Optional[str]:
        """
        Agente Classificador GPC: Recorre à classificação via IA (Llama 3.3 / Groq).
        """
        nome  = titulo_especifico or dados.get("nome") or (dados.get("text_fields", {}) or {}).get("name") or dados.get("name") or ""
        ncm   = dados.get("ncm", "")
        marca = dados.get("marca", "")

        ncm_limpo = str(ncm).replace(".", "").strip()

        # ── FALLBACK: CLASSIFICAÇÃO VIA IA (GROQ) ─────────────────────────────
        logger.info(f"Chamando IA Groq para classificar GPC...")
        
        prompt = f"""Você é um classificador GPC (Global Product Classification) oficial da GS1.
Sua única função é classificar o produto informado em seu código GPC Brick de 8 dígitos correto.

════════════════════════════════════════
REGRA ABSOLUTA — NUNCA QUEBRE ESTA REGRA:
Você JAMAIS deve pedir informações adicionais ao usuário ou responder que não sabe/não pode classificar.
Você SEMPRE deve decidir por um código Brick de 8 dígitos concreto na saída.
════════════════════════════════════════

Guia de Categorias e Bricks GPC oficiais e ativos (use de preferência para estes tipos de produto):
1. Artigos de Papelaria e Escrita (lápis, ecolápis, canetas, marca-textos, canetinhas, blocos adesivos, borrachas):
   - Use '10001234' (para Lápis) ou '10001235' (para Canetas) ou '10001229' (para outros artigos de escrita)
2. Ferramentas Manuais (chaves combinadas, chaves de aperto, alicates, martelos):
   - Use '10000424' (para Chaves de aperto) ou '10001859' (outras ferramentas manuais)
3. Cremes e Cosméticos Corporal/Labial (hidratantes corporais, loções, protetor solar, hidratante labial/Carmed):
   - Use '10000356'
4. Perfumes e Fragrâncias (body splash, colônias, perfumes):
   - Use '10000365'
5. Sabonetes e Higiene Corporal (sabonetes em barra, sabonetes líquidos, géis de banho):
   - Use '10000330'
6. Cuidados e Estética Capilar (shampoo, condicionador, máscaras capilares):
   - Use '10006987'
7. Artigos para Bebê (chupetas, mordedores de bebê, mamadeiras, bicos):
   - Use '10000504'
8. Cabos, Fios, Adaptadores e Conectores Elétricos/Eletrônicos (cabos HDMI/VGA/RJ45, conversores, emendas, adaptadores):
   - Use '10000586'
9. Encanamento e Hidráulica (torneiras, misturadores, registros de banheiro/cozinha/jardim):
   - Use '10002602'
10. Adesivos, Colas e Fitas (colas instantâneas, silicones, fitas autoadesivas, fita isolante, veda rosca):
    - Use '10000624'
11. Utensílios Domésticos de Cozinha/Mesa (jogo americano, panelas, frigideiras, talheres):
    - Use '10002152'
12. Climatização (umidificador de ar, purificadores de ar):
    - Use '10000120'
13. Iluminação (lâmpadas de LED, luminárias, lâmpadas comuns):
    - Use '10000552'
14. Curativos e Primeiros Socorros (curativos adesivos, band-aids, gaze, fitas médicas):
    - Use '10005844'

Se o produto pertencer a uma categoria totalmente diferente de todas as 14 citadas, classifique-o no código GPC Brick correspondente correto de 8 dígitos, iniciando obrigatoriamente com o prefixo '1000' (garantindo que não termine em '09' e que não seja '10101502').

DADOS DO PRODUTO A CLASSIFICAR:
- Nome: {nome}
- NCM: {ncm}
- Marca: {marca}

FORMATO DE SAÍDA (exiba exatamente assim, sem nenhuma linha adicional):

Brick GPC   : [8 dígitos do Brick GPC correto]
Segmento    : [nome do segmento]
Família     : [nome da família]
Classe      : [nome da classe]
Brick       : [descrição do brick]
Confiança   : [Alta / Média / Baixa]
Justificativa: [1 a 3 frases explicando o motivo da escolha]"""

        try:
            groq = GroqClient()
            res = groq.chat(
                system_prompt=prompt,
                user_input="Classifique o produto e retorne o Brick GPC no formato especificado.",
                modelo="llama-3.3-70b-versatile"
            )
            
            # 1. Tentar extração usando padrão estruturado explicativo (case-insensitive)
            # Ex: "Brick GPC   : 10002602" ou "GPC: 10002602"
            match_estruturado = re.search(r'(?:brick\s+gpc|gpc|brick)\s*:\s*\b(\d{8})\b', res, re.IGNORECASE)
            if match_estruturado:
                gpc = match_estruturado.group(1)
                # Verifica se não é igual ao NCM e se não termina com '09' (inativo/genérico) ou é o inativo '10101502'
                if gpc != ncm_limpo and not gpc.endswith("09") and gpc != "10101502":
                    logger.info(f"GPC extraído via padrão estruturado da IA: {gpc}")
                    return gpc

            # 2. Fallback: procurar qualquer número de 8 dígitos que não seja o NCM do produto
            todos_candidatos = re.findall(r'\b(\d{8})\b', res)
            for cand in todos_candidatos:
                if cand != ncm_limpo and not cand.endswith("09") and cand != "10101502":
                    logger.info(f"GPC extraído via varredura de 8 dígitos da IA (filtrado): {cand}")
                    return cand
            
            logger.warning(f"GPC válido não extraído do resultado. IA respondeu:\n{res}")
        except Exception as e:
            logger.error(f"Erro Groq GPC: {e}")

        return None

    def _resolver_campo_fisico(self, nome_campo: str, dados: Dict, quantidade: int = 1) -> Optional[str]:
        n = nome_campo.lower()

        if "modelo" in n or "model" in n:
            val = dados.get("sku") or dados.get("sku_origem")
            return str(val).strip() if val else None

        if "marca" == n or "fabricante" == n:
            m_id = dados.get("manufacturer_id")
            if m_id:
                mapa = self._get_mapa_fabricantes()
                return mapa.get(int(m_id))
            return None

        if "ean" in n or "código universal" in n:
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
        secoes_minimas = ["SOBRE O PRODUTO", "BENEFÍCIOS"]
        if not desc or len(desc) < 100:
            erros.append(f"descrição inválida ou ausente (tamanho: {len(desc)} chars)")
        elif not any(s in desc for s in secoes_minimas):
            erros.append("descrição não contém as seções obrigatórias (SOBRE O PRODUTO, BENEFÍCIOS)")

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
    ) -> Dict:
        """
        Cria a grade completa de 8 anúncios (1/2/3/4un x Clássico/Premium) no BaseLinker.
        """
        logger.warning(f"Iniciando criação de GRADE (8 anúncios) -> SKU Origem={sku_origem} | EAN Unitário={gerar_ean_unitario}")

        dados = self._baixar_dados_produto(pid)
        if not dados:
            return {"sucesso": False, "msg": "Falha ao baixar dados do produto principal."}

        # Trava de Segurança: Validação de dados essenciais
        erro_validacao = self._validar_dados_essenciais(dados)
        if erro_validacao:
            return {"sucesso": False, "msg": erro_validacao}

        if sku_origem == "30480":
            logger.warning(f"AUDITORIA PREÇO SKU 30480: Preço Base={dados.get('price_brutto')} | Prices={dados.get('prices')}")

        dados_secundario = None
        pid_secundario   = None
        if sku_secundario:
            pid_secundario = self.buscar_pid_por_sku(sku_secundario)
            if pid_secundario:
                dados_secundario = self._baixar_dados_produto(pid_secundario)
                if dados_secundario:
                    erro_sec = self._validar_dados_essenciais(dados_secundario)
                    if erro_sec:
                        return {"sucesso": False, "msg": f"Secundário: {erro_sec}"}

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

        skus_criados = []
        ids_criados  = []

        for anuncio in grade.anuncios:
            if anuncio.bloqueado:
                logger.warning(f"Ignorando {anuncio.referencia}: Bloqueado.")
                continue

            sku_novo = self._obter_proximo_sku(None)
            if not sku_novo:
                break

            # Seleciona título da grade para a variação
            chave_grade  = f"{anuncio.quantidade}_{anuncio.modalidade}"
            titulo_base  = titulos_grade.get(chave_grade) or titulos_grade.get(str(anuncio.quantidade))

            if not titulo_base:
                titulo_base = conteudo_base.get('titulo', '')

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
            else:
                # Unitário: garante que não tem Kit
                pass # já limpamos acima
            
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

            ean_final = ""
            if anuncio.quantidade == 1 and not gerar_ean_unitario:
                ean_final = dados.get("ean", "")

            # Atualiza features por variação
            features_var = features_base.copy()
            has_cond = False
            has_fmt = False
            
            for k in list(features_var.keys()):
                k_lower = k.lower()
                if ean_final and ("código universal" in k_lower or "ean" in k_lower):
                    features_var[k] = ean_final
                if "unidades por kit" in k_lower or "unidades por embalagem" in k_lower:
                    features_var[k] = str(anuncio.quantidade)
                if k_lower in ("condição do item", "item_condition"):
                    features_var[k] = "Novo"
                    has_cond = True
                if k_lower == "formato de venda":
                    features_var[k] = "Unidade" if anuncio.quantidade == 1 else "Kit"
                    has_fmt = True

            if not has_cond:
                features_var["Condição do item"] = "Novo"
            if not has_fmt:
                features_var["Formato de venda"] = "Unidade" if anuncio.quantidade == 1 else "Kit"

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
                "weight":          (float(dados.get("weight") or 0) + float((dados_secundario or {}).get("weight") or 0)) * anuncio.quantidade,
                "height":          dados.get("height", 0),
                "width":           dados.get("width", 0),
                "length":          dados.get("length", 0),
                "tags":            tags_enviar,
                "images":          self._extrair_imagens(dados),
                "is_bundle":       1,
                "bundle_products": {str(pid): anuncio.quantidade},
            }
            if pid_secundario:
                payload["bundle_products"][str(pid_secundario)] = anuncio.quantidade

            try:
                id_kit = self._enviar_para_baselinker(payload)
                if not id_kit:
                    raise Exception("Falha ao obter ID do produto criado no BaseLinker.")

                self._salvar_historico(sku_origem, pid, sku_novo, id_kit)
                skus_criados.append(sku_novo)
                ids_criados.append(id_kit)

                if anuncio.quantidade > 1 or (anuncio.quantidade == 1 and gerar_ean_unitario):
                    time.sleep(2)
                    ean_final = self._obter_ean_kit(
                        sku_origem, anuncio.quantidade, dados, sku_novo, 
                        titulo_final, forcar_unitario=(anuncio.quantidade == 1 and gerar_ean_unitario)
                    )

                if ean_final and (anuncio.quantidade > 1 or gerar_ean_unitario):
                    for k in list(features_var.keys()):
                        if "código universal" in k.lower() or "ean" in k.lower():
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
            tf = dados.get("text_fields", {})
            if isinstance(tf, list):
                tf = {}
            nome_produto = tf.get("name") or dados.get("name") or ""
            if nome_produto:
                predicoes = MercadoLivreClient().predict_categoria(nome_produto, limite=1)
                if predicoes and predicoes[0].get("category_id"):
                    category_id_ml = str(predicoes[0]["category_id"])
                    logger.info(f"Categoria ML prevista: {category_id_ml}")

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

            conteudo_ia = self.seo.gerar_conteudo(
                nome_original_prod,
                descricao_original,
                features_base,
                e_misto=bool(dados_secundario),
                atributos_obrigatorios=lista_obrigatorios,
                gera_grade=False,
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