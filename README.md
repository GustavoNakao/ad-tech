# Projeto S-Produto

Este repositório contém o sistema de automação para criação de kits e integração com o Mercado Livre via BaseLinker.

## Visão Geral
O sistema automatiza a criação de grades de anúncios, otimização de títulos com IA, registro de EAN na GS1 e precificação dinâmica através do Motor R831.

---

# Documentação Técnica — Módulo `criacao_estruturado`

## Visão Geral

O módulo `criacao_estruturado` é responsável pela criação automatizada de **grades de kits** no BaseLinker, com integração ao Mercado Livre. A partir de um único produto base, o sistema gera 8 variações de anúncio (1/2/3/4 unidades × Clássico/Premium), com títulos otimizados para SEO, descrição persuasiva, ficha técnica preenchida por IA, precificação automática via Motor R831 v2 e registro de EAN/GTIN na GS1 Brasil.

---

## Estrutura de Arquivos

```
criacao_estruturado/
├── services/
│   └── criador_kits_service.py   # Core: SEOGenerator + CriadorKitsService
├── models/
│   ├── historico_kit.py          # Registro de kits criados
│   ├── gtin_kit.py               # Cache de GTINs/EANs por kit
│   ├── numero_reciclado.py       # SKUs com falha para reaproveitamento
│   ├── baselinker_kit_sku.py     # Cache de SKUs no BaseLinker
│   └── controle_sequencia.py    # Sequência de SKUs (legado)
├── views.py                      # Painel web de criação de kits
22: └── urls.py
```

---

## Modelos

### `HistoricoKit`
Registra cada kit criado com sucesso.

| Campo | Tipo | Descrição |
|---|---|---|
| `sku_origem` | CharField(60) | SKU do produto base |
| `id_origem` | CharField(50) | PID do produto base no BaseLinker |
| `sku_kit` | CharField(60) | SKU do kit criado (único) |
| `id_kit` | CharField(50) | PID do kit no BaseLinker |
| `criado_em` | DateTimeField | Auto preenchido na criação |

### `GtinKit`
Persistência de GTINs gerados na GS1 Brasil. Garante que o mesmo par (produto + quantidade) sempre use o mesmo GTIN entre Clássico e Premium.

| Campo | Tipo | Descrição |
|---|---|---|
| `sku_origem` | CharField(60) | SKU do produto base |
| `quantidade` | IntegerField | Quantidade do kit (2, 3 ou 4) |
| `gtin` | CharField(20) | EAN gerado (único) |
| `criado_em` | DateTimeField | Auto preenchido |

**Regra:** `unique_together = ['sku_origem', 'quantidade']` — um GTIN por combinação.

### `NumeroReciclado`
Tabela de SKUs que falharam durante a criação e devem ser reaproveitados na próxima tentativa.

| Campo | Tipo | Descrição |
|---|---|---|
| `numero` | PositiveIntegerField | SKU a reciclar (único) |

### `BaselinkerKitSku`
Cache local dos SKUs de kits existentes no BaseLinker, usado para reconciliação.

| Campo | Tipo | Descrição |
|---|---|---|
| `sku` | PositiveIntegerField | SKU do kit (único, indexado) |
| `product_id` | CharField(50) | PID no BaseLinker |
| `sincronizado_em` | DateTimeField | Última sincronização |

### `ControleSequencia`
Controla o próximo número de SKU a ser emitido (legado — substituído pela função PostgreSQL `get_next_kit_sku()`).

| Campo | Tipo | Descrição |
|---|---|---|
| `nome` | CharField | Identificador da sequência |
| `proximo_numero` | PositiveIntegerField | Próximo SKU a usar |

**Métodos:**
- `get_sequencia()` — retorna ou cria o objeto de sequência
- `avancar()` — retorna o número atual e incrementa +1

---

## Constantes Globais

| Constante | Valor | Descrição |
|---|---|---|
| `PRICE_IDS` | Lista de 25 IDs | IDs de tabelas de preço ativas no BaseLinker |
| `SECOES_OBRIGATORIAS` | 5 seções | Seções que a descrição deve conter |
| `SEOGenerator.ID_INVENTARIO` | `24542` | ID do inventário no BaseLinker |
| `SEOGenerator.ID_CATEGORIA_KITS` | `979589` | Categoria padrão de kits no BaseLinker |
| `SEOGenerator.MAX_RETRIES` | `3` | Tentativas máximas para chamadas à API |

---

## Funções Utilitárias (módulo nível)

### `ajustar_titulo(titulo, min_c=55, max_c=60) -> str`
Garante que o título fique entre 55 e 60 caracteres.
- **Corte:** remove a última palavra inteira enquanto `len > max_c`
- **Padding SEO:** adiciona palavras de reforço (`Alta Qualidade`, `Pronta Entrega`, etc.) enquanto `len < min_c`, sem repetir e sem palavras proibidas

### `formatar_title_case(titulo) -> str`
Aplica Title Case preservando siglas (`LED`, `USB`, `HDMI`, `FM`, `UV`, etc.).

### `validar_descricao(desc) -> bool`
Verifica se as 5 seções obrigatórias estão presentes:
`SOBRE O PRODUTO`, `BENEFÍCIOS`, `DIFERENCIAIS DO PRODUTO`, `DÚVIDAS FREQUENTES`, `IDEAL PARA`

### `normalizar_descricao(desc) -> str`
Remove lixo antes de `SOBRE O PRODUTO`, garantindo que a descrição sempre inicie pela seção correta.

---

## Classe `SEOGenerator`

Orquestrador de agentes de IA especializados. Usa **Gemini 2.5 Flash** para Compliance, Títulos e Descrição, e **Groq (Llama)** para Ficha Técnica e Keywords.

### Agentes de IA

| Agente | Modelo | Função |
|---|---|---|
| **Agente 1 — Compliance** | Gemini 2.5 Flash | Verifica se o produto pode ser anunciado no ML |
| **Agente 2A — Título Unitário** | Gemini 2.5 Flash | Gera 1 título otimizado (55–60 chars) |
| **Agente 2B — Grade de Títulos** | Gemini 2.5 Flash | Gera 8 títulos base (t1_a→t4_b) |
| **Agente 4 — Ficha Técnica** | Groq Llama 3.3 70B | Extrai/infere atributos técnicos |
| **Agente 5 — Keywords** | Groq Llama 3.1 8B | Gera lista de palavras-chave SEO |
| **Agente 6 — Descrição** | Gemini 2.5 Flash | Cria descrição completa com 5 seções |

### Regras do Agente 1 — Compliance
Bloqueia produtos nas categorias: Financeiro, Digital/Software, Segurança/Saúde, Propriedade Intelectual, Não-Produto, Adulto, Animais/Meio Ambiente.
Retorna JSON: `{"status": "ok"|"bloqueado"|"revisar", ...}`

### Regras do Agente 2A — Título Unitário
- 55 a 60 caracteres obrigatórios
- Proibido: `Premium`, `Clássico`, `Original`, `Pack`, `Kit` (no unitário), caracteres especiais
- Estrutura: Palavra-chave principal → Linha → Capacidade → Quantidade → Reforço
- 3 tentativas; na falha, aplica `ajustar_titulo()` em Python

### Regras do Agente 2B — Grade de Títulos
- Gera 8 títulos neutros (sem quantidade) com ~45–55 chars
- Chaves retornadas: `t1_a`, `t1_b`, `t2_a`, `t2_b`, `t3_a`, `t3_b`, `t4_a`, `t4_b`
- Mapeamento interno: `t1_a → 1_classico`, `t1_b → 1_premium`, etc.
- Inclui campo `estrategia_seo` obrigatório antes dos títulos
- Fallback: usa o nome original formatado se falhar

### Regras do Agente 4 — Ficha Técnica
- Usa **exatamente** os nomes dos atributos fornecidos como chaves
- Valores de lista permitida → deve escolher um dos valores exatos
- Atributo inferível → adiciona `(inferido)` ao valor
- Sem dados → `"A informar"`
- Nunca extrai `EAN`, `GTIN`, `Código de barras`
- Padronização: cores em PT-BR, medidas com unidade, booleanos `"Sim"/"Não"`

### Regras do Agente 5 — Keywords
- Categorias: Termos Primários, Secundários, de Kit, Alternativos
- Para kits: inclui obrigatoriamente `"kit N [produto]"` e `"conjunto N [produto]"`
- Mín. 12, máx. 25 termos; todos em minúsculas, sem pontuação

### Regras do Agente 6 — Descrição (Copywriter)
- Template fixo com 5 seções em ordem imutável
- Máx. 2800 caracteres
- BENEFÍCIOS: começam com verbo no presente (Garante, Elimina, Protege...)
- DIFERENCIAIS: formato `Característica — benefício`
- Proibido: adjetivos vazios, clichês de marketing, EAN/SKU no texto
- 3 tentativas; na última aceita o melhor disponível

### Método `gerar_conteudo()`
```python
gerar_conteudo(
    nome_original,         # Nome do produto base
    descricao_atual="",    # Descrição existente (fallback)
    features=None,         # Atributos já conhecidos
    qtd_kit=1,             # Quantidade do kit
    e_misto=False,         # Kit com dois produtos diferentes
    atributos_obrigatorios=None,  # Campos obrigatórios do ML
    gera_grade=False,      # True=gera 8 títulos, False=1 título
) -> Dict
```

---

## Classe `CriadorKitsService`

Orquestrador principal da criação de kits. Integra BaseLinker, Mercado Livre, GS1 Brasil, Motor de Precificação R831 e `SEOGenerator`.

### Constantes de Instância

| Constante | Valor | Descrição |
|---|---|---|
| `ID_INVENTARIO` | `24542` | Inventário BaseLinker |
| `ID_CATEGORIA_KITS` | `979589` | Categoria padrão de kits |
| `MAX_RETRIES` | `3` | Tentativas por chamada de API |
| `DELAY_RETRY` | `1.0s` | Pausa entre retentativas |
| `DELAY_RATE_LIMIT` | `3.0s` | Pausa ao receber HTTP 429 |

---

### Métodos de Validação (Travas de Segurança)

#### `_validar_dados_essenciais(dados) -> Optional[str]`
Bloqueia a criação se o produto base não tiver:
- `imagem_url` (imagem)
- `weight > 0` (peso)
- `height > 0` (altura)
- `width > 0` (largura)

#### `_validar_conteudo_ia(conteudo, nome_original) -> Optional[str]`
Valida se a IA gerou conteúdo válido antes de consumir SKU:
- **Título:** não vazio, mínimo 10 chars
- **Descrição:** não vazia, mínimo 100 chars, contém `SOBRE O PRODUTO` e `BENEFÍCIOS`

---

## Fluxo de SKU

```
Banco PostgreSQL → get_next_kit_sku()
        ↓
  Verifica NumeroReciclado (prioridade)
        ↓ se vazio
  Incrementa sequência interna
        ↓
  Retorna próximo SKU disponível
        ↓ em caso de falha da API BaseLinker
  _reciclar_numero(sku) → insere em NumeroReciclado
```

---

## Grade de 8 Anúncios

| Variação | Chave da Grade | Quantidade | Modalidade |
|---|---|---|---|
| Unitário Clássico | `1_classico` | 1 | classico |
| Unitário Premium | `1_premium` | 1 | premium |
| Kit 2 Clássico | `2_classico` | 2 | classico |
| Kit 2 Premium | `2_premium` | 2 | premium |
| Kit 3 Clássico | `3_classico` | 3 | classico |
| Kit 3 Premium | `3_premium` | 3 | premium |
| Kit 4 Clássico | `4_classico` | 4 | classico |
| Kit 4 Premium | `4_premium` | 4 | premium |

**Regras de EAN por variação:**
- Quantidade 1 → usa EAN do produto base
- Quantidade > 1 → gera novo EAN na GS1, compartilhado entre Clássico e Premium do mesmo nível

---

## Dependências Externas

| Integração | Uso |
|---|---|
| `BaselinkerClient` | CRUD de produtos no inventário |
| `MercadoLivreClient` | Busca de categorias e atributos obrigatórios |
| `GS1Client` | Registro de GTINs/EANs oficiais |
| `GeminiClient` | Agentes de Compliance, Título e Descrição |
| `GroqClient` | Agentes de Ficha Técnica, Keywords e GPC |
| `Calculadora` (R831 v2) | Cálculo de preços (promoção + vitrine) |
| `TermosProibidos` | Validação de palavras proibidas no título |
