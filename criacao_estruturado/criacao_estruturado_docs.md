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
└── urls.py
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

Orquestrador de agentes de IA especializados. Usa **Gemini 1.5 Flash** para Compliance, Títulos e Descrição, e **Groq (Llama)** para Ficha Técnica e Keywords.

### Agentes de IA

| Agente | Modelo | Função |
|---|---|---|
| **Agente 1 — Compliance** | Gemini 1.5 Flash | Verifica se o produto pode ser anunciado no ML |
| **Agente 2A — Título Unitário** | Gemini 1.5 Flash | Gera 1 título otimizado (55–60 chars) |
| **Agente 2B — Grade de Títulos** | Gemini 1.5 Flash | Gera 8 títulos base (t1_a→t4_b) |
| **Agente 4 — Ficha Técnica** | Groq Llama 3.3 70B | Extrai/infere atributos técnicos |
| **Agente 5 — Keywords** | Groq Llama 3.1 8B | Gera lista de palavras-chave SEO |
| **Agente 6 — Descrição** | Gemini 1.5 Flash | Cria descrição completa com 5 seções |

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
```
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
**Retorna:**
```python
{
    "titulo":        str,   # Título principal (≤60 chars)
    "titulos_grade": dict,  # {"1_classico": ..., "4_premium": ...}
    "descricao":     str,   # Texto completo com 5 seções
    "atributos":     dict,  # Ficha técnica extraída pela IA
    "keywords":      dict,  # Termos SEO categorizados
}
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

### Métodos de API BaseLinker

#### `_request_seguro(method, params) -> Dict`
Wrapper com retry automático. Detecta HTTP 429 e aplica delay maior (`DELAY_RATE_LIMIT`). Levanta `Exception` após `MAX_RETRIES` falhas.

#### `_baixar_dados_produto(pid) -> Optional[Dict]`
Baixa dados do produto via `getInventoryProductsData` e os padroniza em dicionário plano usando `buscar_produto_por_sku()`. Resolve campos de texto, NCM, Marca e Imagem.

#### `buscar_pid_por_sku(sku) -> Optional[str]`
Busca o PID (Product ID interno do BaseLinker) a partir do SKU via `getInventoryProductsList`. Retorna `None` se não encontrado.

---

### Métodos de Dados do Produto

#### `_extrair_features(dados) -> Dict`
Extrai os atributos técnicos do produto a partir do campo `text_fields.features`. Suporta formato lista de dicts e dict direto.

#### `_extrair_imagens(dados) -> Dict[str, str]`
Extrai URLs de imagens do produto. Suporta formatos dict, lista de strings e lista de dicts. Prefixo `url:` adicionado automaticamente. Retorna apenas URLs válidas (iniciadas com `http`).

#### `_extrair_custo(dados) -> float` (staticmethod)
Tenta extrair o custo do produto na ordem: `average_cost` → `price_brutto` → `price` → campos `prices_*`. Retorna `0.0` se nenhum campo encontrado.

---

### Métodos de Precificação

#### `_calcular_preco(dados, sku_novo) -> float`
Chama `Calculadora.calcular(dados, sku_ref=sku_novo)` e retorna `preco_final`. Em caso de erro, retorna `piso_valor_forcado` das regras ativas.

#### `_montar_prices_dict(preco) -> Dict`
Retorna `{"53516": preco}` — tabela de preço padrão no BaseLinker.

---

### Métodos de Texto/Payload

#### `_montar_campos_texto(dados, sku_novo, dados_secundario=None, category_id_ml=None) -> Dict`
1. Extrai atributos e nome originais
2. Busca atributos obrigatórios do ML via `_buscar_atributos_obrigatorios_ml()`
3. Chama `seo.gerar_conteudo()` para gerar título, descrição e atributos
4. Mescla atributos da IA com os já existentes
5. Marca campos faltantes como `"PREENCHER"` ou `"PREENCHER (Opções: ...)"` se houver valores permitidos
6. Adiciona campo obrigatório `extra_field_39310 = "un"`

#### `_buscar_atributos_obrigatorios_ml(category_id_ml, features, dados) -> List[str]`
Consulta a API do Mercado Livre para obter atributos obrigatórios da categoria.
- **Filtro de Exclusão:** Remove automaticamente campos de identificação redundantes: `Motivo de GTIN vazio`, `reason_for_no_gtin`, `Motivo de GTIN isento`, `EAN`, `GTIN`, `Código de barras`.
- Para cada atributo não preenchido e não filtrado:
  - Se resolúvel por dados físicos → chama `_resolver_campo_fisico()` e preenche diretamente
  - Caso contrário → adiciona à lista para a IA preencher, com `(Valores permitidos: ...)` se aplicável

**Não executa** se `category_id_ml` for `None` ou `"979589"`.

#### `_montar_payload(dados, sku_novo, pid, ...) -> Dict`
Monta o payload completo para `addInventoryProduct` no BaseLinker:
- Preço calculado (simples ou misto)
- Campos de texto gerados por IA
- Imagens extraídas
- Bundle com `pid → quantidade`
- Tags (`classico` ou `premium`)
- Dimensões e peso

#### `_resolver_campo_fisico(nome_campo, dados, quantidade=1) -> Optional[str]`
Resolve campos de ficha técnica a partir dos dados físicos do produto sem precisar de IA:

| Campo (nome_campo) | Fonte |
|---|---|
| `marca` / `fabricante` | `manufacturer_id` → mapa de fabricantes |
| `ean` / `código universal` | `dados["ean"]` |
| `unidades por kit/embalagem` | `quantidade` passado como parâmetro |
| `altura` | `dados["height"]` + `" cm"` |
| `largura` | `dados["width"]` + `" cm"` |
| `comprimento` / `profundidade` | `dados["length"]` + `" cm"` |
| `peso` | `dados["weight"]` em `kg` ou `g` (se < 1 kg) |

---

### Métodos de EAN/GS1

#### `_obter_ean_kit(sku_origem, quantidade, dados_completos, sku_kit_final, titulo_final, forcar_unitario=False) -> str`
**Regra:** kits com `quantidade <= 1` não geram novo EAN (retorna `""`), **exceto** se `forcar_unitario` for `True`.

Para `quantidade > 1`:
1. Verifica se já existe EAN em `GtinKit` para o par `(sku_origem, quantidade)`
2. Se existir e não for `PENDENTE` → reaproveita
3. Se não existir:
   - Descobre GPC via `_descobrir_gpc_via_ia()`
   - Resolve marca real via mapa de fabricantes
   - Chama `gs1_client.gerar_ean_completo()`
   - Salva o GTIN no banco
4. **Fallback UUID:** se a GS1 falhar, gera GTIN determinístico via `uuid5` com seed `kit-{sku}-{qtd}`

#### `_descobrir_gpc_via_ia(dados) -> str`
Usa Groq (Llama 3.3 70B) para classificar o produto no GPC (Global Product Classification) de 8 dígitos.

**Regras:**
- Retorna apenas 8 dígitos numéricos
- Rejeita códigos terminados em `09` (a GS1 Brasil rejeita)
- Fallback: `"10005844"` (Higiene/Limpeza Pet)

**Base de conhecimento hardcoded:**
- Higiene/Limpeza Pet → `10005844`
- Ração Cães → `10000104`
- Ração Gatos → `10000105`
- Antiparasitários → `10005845`
- Brinquedos Pet → `10000570`
- Coleiras/Acessórios → `10005691`

---

### Métodos de Categoria

#### `sincronizar_categoria_ml_para_baselinker(category_id_ml) -> int`
Sincroniza a hierarquia de categorias do ML para o BaseLinker:
1. Busca o `path_from_root` da categoria no ML
2. Percorre nível a nível, verificando se cada categoria já existe no BaseLinker
3. Se não existir → cria via `addInventoryCategory`
4. Retorna o ID do último nível criado/encontrado
5. **Fallback:** retorna `ID_CATEGORIA_KITS` em qualquer erro ou se `category_id_ml` for `None`/`"979589"`

---

### Métodos de Validação (Travas de Segurança)

#### `_validar_dados_essenciais(dados) -> Optional[str]`
Bloqueia a criação se o produto base não tiver:
- `imagem_url` (imagem)
- `weight > 0` (peso)
- `height > 0` (altura)
- `width > 0` (largura)

Retorna mensagem de erro descritiva ou `None` se OK.

#### `_validar_conteudo_ia(conteudo, nome_original) -> Optional[str]`
Valida se a IA gerou conteúdo válido antes de consumir SKU:
- **Título:** não vazio, mínimo 10 chars
- **Descrição:** não vazia, mínimo 100 chars, contém `SOBRE O PRODUTO` e `BENEFÍCIOS`

Retorna mensagem de erro ou `None` se OK.

---

### Métodos de Utilitários

#### `_obter_proximo_sku(sku_desejado) -> Optional[str]`
- Se `sku_desejado` informado → retorna esse SKU diretamente
- Caso contrário → chama função PostgreSQL `get_next_kit_sku()` via raw SQL
- Retorna `None` em caso de falha

#### `_enviar_para_baselinker(payload) -> Optional[str]`
Chama `addInventoryProduct` e retorna o `product_id` do kit criado. Levanta `Exception` se não receber `product_id`.

#### `_salvar_historico(sku_origem, id_origem, sku_kit, id_kit) -> None`
Persiste o registro de criação em `HistoricoKit`. Erros são apenas logados, não propagados.

#### `_reciclar_numero(sku) -> None`
Insere o SKU na tabela `NumeroReciclado` via SQL com `ON CONFLICT DO NOTHING`. Só processa SKUs numéricos.

#### `_get_vendas_60_dias(sku) -> int`
Consulta `ItemPedido` para contar quantidade total vendida nos últimos 60 dias. Usado pelo Motor R831 v2 para cálculo de tarifa reduzida. Retorna `0` em caso de erro.

#### `_get_mapa_tags() -> Dict[str, int]`
Carrega e cacheia o mapa de tags do BaseLinker (`obter_mapa_tags()`). Tags usadas: `"classico"` e `"premium"`.

#### `_get_mapa_fabricantes() -> Dict[int, str]`
Carrega e cacheia fabricantes do inventário via `getInventoryManufacturers`. Mapa: `{manufacturer_id: nome}`.

---

### Método Principal — `executar_criacao_grade()`

```
executar_criacao_grade(
    pid,                   # PID do produto base no BaseLinker
    sku_origem,            # SKU do produto base
    sku_secundario=None,   # SKU do 2º produto (Kit Misto)
    category_id_ml=None,   # Category ID do Mercado Livre
    gerar_ean_unitario=False, # Se True, gera novo GTIN para Qtd 1
) -> Dict
```

**Fluxo completo:**

```
1. Baixar dados do produto base
2. TRAVA: _validar_dados_essenciais() — bloqueia se faltar imagem/peso/altura/largura
3. Se Kit Misto: baixar e validar produto secundário
4. Resolução de categoria ML:
   - Se não informada/padrão → predict_categoria() via nome do produto
5. Sincronizar categoria ML → BaseLinker
6. Calcular grade de preços via Calculadora.calcular_grade()
   - Inclui vendas_60_dias para tarifa reduzida
   - Kit Misto: soma custos e pesos
7. BLOQUEIO: se grade.bloqueado → retorna erro
8. Gerar conteúdo base (1 chamada à IA para todos os 8 anúncios):
   - gerar_conteudo(..., gera_grade=True) → 8 títulos + 1 descrição + ficha técnica
9. TRAVA: _validar_conteudo_ia() — bloqueia se IA não gerou conteúdo válido
10. Para cada anuncio na grade (8 variações):
    a. Obter próximo SKU via get_next_kit_sku()
    b. Selecionar título da grade por chave "{quantidade}_{modalidade}"
    c. Limpeza agressiva do título: remove Kit, Unidade, Unitário, Premium, Clássico, Pack, Original
    d. Adicionar prefixo "Kit N" para quantidade > 1
    e. ajustar_titulo() — padding/corte para 55–60 chars
    f. Determinar tag (classico/premium)
    g. Montar payload completo
    h. _enviar_para_baselinker() → obter id_kit
    i. _salvar_historico()
    j. Se quantidade > 1 ou (quantidade == 1 e gerar_ean_unitario):
       Aguardar 2s e gerar EAN via _obter_ean_kit()
    k. Se EAN gerado: atualizar produto no BaseLinker com EAN e ficha técnica
    l. Em erro: _reciclar_numero() e continuar

Retorna:
{
    "sucesso": True,
    "skus": ["15063", "15064", ...],
    "ids": ["pid1", "pid2", ...],
    "msg": "Grade criada com N anúncios no BaseLinker."
}
```

### Método Legado — `executar_criacao()`

Cria um **único kit** (não uma grade). Fluxo similar ao `executar_criacao_grade()`, mas:
- Chama `gerar_conteudo(..., gera_grade=False)` → 1 título apenas
- Tenta até 5 vezes em caso de SKU duplicado
- Retorna `sku_novo` e `id_kit` únicos

---

## View — `painel_kits`

Painel web protegido por `@cadastro_required`.

### Ação `analisar`
1. Recebe SKU manual (+ SKU secundário, URL do ML e `gerar_ean_unitario` opcionais)
2. Busca PID no BaseLinker
3. Resolve `category_id_ml` via URL do produto (ou usa padrão)
4. Chama `_analisar_com_grade()` → retorna grade de preços sem criar nada
5. Exibe status: 🟢 Verde / 🟡 Amarelo / 🔴 Vermelho
6. Armazena `gerar_ean_unitario` para a próxima etapa.

### Ação `confirmar_criacao`
1. Recebe SKU + category_id_ml + `gerar_ean_unitario` do formulário
2. Chama `servico.executar_criacao_grade(..., gerar_ean_unitario=...)`
3. Exibe mensagem de sucesso com SKUs criados

### Função `_analisar_com_grade()`
Calcula grade de preços sem salvar nada. Retorna:
- `anuncios[]`: lista com 8 variações (preço promoção, vitrine, custo logístico, tarifa reduzida)
- `preco_unitario`, `preco_kit_2`, `preco_kit_3`
- `preco_unit_kit2`, `preco_unit_kit3` (preço por unidade no kit)
- `alerta_upsell`: se Kit 2 estiver menos de R$10 acima do unitário
- `status`: verde/amarelo/vermelho com emoji e mensagem

**Regras de status:**
- 🟢 Verde: `preco_promocao >= piso_frete_gratis` E `piso não foi forçado`
- 🔴 Vermelho: `unit.bloqueado` ou `unit` não existe
- 🟡 Amarelo: demais casos (piso aplicado mas ainda lucrativo)

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
- Quantidade 1 → usa EAN do produto base (**ou gera novo se `gerar_ean_unitario` for True**)
- Quantidade > 1 → gera novo EAN na GS1, compartilhado entre Clássico e Premium do mesmo nível

**Peso por variação:** `peso_base × quantidade` (+ peso_secundario × quantidade se Kit Misto)

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
