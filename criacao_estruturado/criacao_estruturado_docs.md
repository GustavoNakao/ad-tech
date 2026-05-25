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
│   ├── ncm_gpc.py                # Mapeamento permanente NCM -> GPC
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
Persistência de GTINs gerados na GS1 Brasil. Mapeia e rastreia o GTIN gerado individualmente por SKU final de kit (`sku_kit`).

| Campo | Tipo | Descrição |
|---|---|---|
| `sku_origem` | CharField(60) | SKU do produto base |
| `sku_kit` | CharField(60) | SKU único do kit gerado (Indexado) |
| `quantidade` | IntegerField | Quantidade do kit (2, 3 ou 4) |
| `gtin` | CharField(20) | EAN gerado (compartilhado por Clássico/Premium no mesmo lote) |
| `criado_em` | DateTimeField | Auto preenchido |

**Regra de Unicidade:** O modelo indexa individualmente por `sku_kit` (a restrição `unique_together` de `sku_origem` e `quantidade` foi removida para permitir que lotes de grade criados em momentos diferentes gerem novos GTINs independentes para o mesmo produto base).

### `NcmGpc`
Mapeamento permanente e dinâmico de NCM (8 dígitos) para o código GPC correspondente (8 dígitos). Substitui a classificação baseada em Inteligência Artificial.

| Campo | Tipo | Descrição |
|---|---|---|
| `ncm` | CharField(20) | NCM do produto (Unique, Indexado) |
| `gpc` | CharField(20) | Código GPC correspondente (Indexado) |
| `criado_em` | DateTimeField | Auto preenchido |

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
Verifica se as 5 seções obrigatórias estão presentes na descrição. A validação é flexível e robusta, utilizando uma função de normalização que ignora caixa alta/baixa, marcas de markdown (`#`, `*`, `:`), acentos e espaços extras para evitar falsos positivos de seções ausentes geradas por IA.
As seções verificadas são: `SOBRE O PRODUTO`, `BENEFÍCIOS`, `DIFERENCIAIS DO PRODUTO`, `DÚVIDAS FREQUENTES`, `IDEAL PARA`.

### `normalizar_descricao(desc) -> str`
Remove lixo e conteúdo indesejado antes do início da seção `SOBRE O PRODUTO`, garantindo que a descrição sempre inicie pela seção correta. Suporta de forma robusta variações de caixa (maiúscula/minúscula) e formatações markdown no cabeçalho da seção.

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
- **Resiliência contra cache corrompido:** utiliza `use_cache=(tentativa == 0)`. Se a primeira tentativa (que lê do cache) falhar nos critérios de validação, as tentativas subsequentes ignoram o cache e consultam a API em tempo real.

### Regras do Agente 2B — Grade de Títulos
- Gera 8 títulos neutros (sem quantidade) com ~45–55 chars
- Chaves retornadas: `t1_a`, `t1_b`, `t2_a`, `t2_b`, `t3_a`, `t3_b`, `t4_a`, `t4_b`
- Mapeamento interno: `t1_a → 1_classico`, `t1_b → 1_premium`, etc.
- Inclui campo `estrategia_seo` obrigatório antes dos títulos
- Fallback: usa o nome original formatado se falhar
- **Resiliência contra cache corrompido:** utiliza `use_cache=(tentativa == 0)`. Se a primeira tentativa (que lê do cache) falhar no parser de JSON ou nos critérios de grade, as tentativas subsequentes ignoram o cache e realizam chamadas diretas à API.

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
- **Resiliência contra cache corrompido:** utiliza `use_cache=(tentativa == 0)`. Se o cache retornar uma descrição incompleta ou inválida, a tentativa seguinte forçará a consulta em tempo real da API para re-gerar e atualizar o cache com sucesso.

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

#### `_obter_ean_kit(sku_origem, quantidade, dados_completos, sku_kit_final, titulo_final, forcar_unitario=False, gpc_manual=None) -> str`
**Regra:** kits com `quantidade <= 1` não geram novo EAN (retorna `""`), **exceto** se `forcar_unitario` for `True`.

Para `quantidade > 1` ou unitários com `forcar_unitario=True` (Orquestração de Lote, Compartilhamento Resiliente e Unicidade):
1. **Busca Inteligente por Quantidade no Banco Local:** Antes de chamar a GS1 ou gerar novo código, o método realiza um lookup persistente na tabela local `GtinKit` filtrando por `sku_origem` e `quantidade` (excluindo EANs `PENDENTE` ou vazios). Se já existir um EAN gerado para aquela quantidade (ex: gerado pela variação clássica em um lote anterior ou execução parcial), ele é reaproveitado e herdado imediatamente. Isso garante 100% de consistência de compartilhamento de EAN entre Clássico e Premium mesmo em caso de falhas parciais ou execuções separadas, reduzindo drasticamente o consumo de créditos na GS1.
2. **Cache de Lote (Execução):** Adicionalmente, no método `executar_criacao_grade`, mantemos um cache local em memória (`self.gtins_gerados_lote: Dict[int, str]`). Se a quantidade já teve um EAN gerado na mesma execução (ex: Clássico acabou de gerar para Qtd 2), a variação correspondente (ex: Premium) herda esse EAN em memória instantaneamente e registra a entrada histórica no banco local individualmente para o seu `sku_kit`.
3. **Resolução de GPC (Sem IA):**
   - **GPC Manual:** Tenta obter do parâmetro `gpc_manual` (passado a partir do painel na criação). Se for válido (8 dígitos), salva de forma definitiva e permanente na tabela local `NcmGpc`.
   - **Mapeamento Local:** Se não foi fornecido manual ou for inválido, busca na tabela local `NcmGpc` pelo NCM do produto base.
   - **Fallback Estático:** Se não houver mapeamento local, busca no dicionário estático local por faixas de NCM (`_obter_fallback_gpc_por_ncm`). Ao encontrar, grava imediatamente na tabela local `NcmGpc` para persistência definitiva futura.
   - **Exceção:** Se o NCM for inédito e nenhum GPC foi fornecido ou sugerido, lança uma exceção exigindo o preenchimento manual no painel.
4. **Persistência Local de GTIN:** O método consulta e persiste em `GtinKit` usando `sku_kit=sku_kit_final` como chave de busca, garantindo reaproveitamento caso este SKU específico do kit já tenha sido registrado anteriormente.
5. **Tratamento Seguro de Falha na GS1 (Evita Rejeições):** Se a API da GS1 falhar (por timeout ou limite de conexões): para anúncios unitários (Qtd = 1), o sistema resgata e reutiliza o EAN original do produto base como fallback se for válido; para kits (Qtd > 1) ou se o unitário não tiver EAN, o sistema registra como 'PENDENTE' no banco local e retorna uma string vazia `""`. Isso impede a injeção de GTINs com formatos inválidos (ex: iniciados com '1') que seriam rejeitados duramente pelos marketplaces como o Mercado Livre.

#### `_obter_fallback_gpc_por_ncm(ncm_limpo) -> Optional[str]`
Retorna o código GPC de fallback para NCMs comuns do estoque com base em regras estáticas (ex: `8481...` -> `10002602` para Torneiras/Misturadores, `8544...` -> `10000586` para Cabos/Conectores, etc.).

**Taxonomia Base de Conhecimento:**
7. **Artigos para Bebê (Chupetas/Mordedores):** `10000504`.
8. **Cabos, Fios, Adaptadores e Conectores:** `10000586`.
9. **Encanamento e Hidráulica (Torneiras/Registros):** `10002602`.
10. **Adesivos, Colas e Fitas:** `10000624`.
11. **Utensílios Domésticos de Cozinha/Mesa:** `10002152`.
12. **Climatização (Umidificadores):** `10000120`.
13. **Iluminação (Lâmpadas/Luminárias):** `10000552`.
14. **Curativos e Primeiros Socorros:** `10005844`.


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
- **Título:** não vazio, mínimo 10 chars.
- **Descrição:** não vazia, mínimo 100 chars. Deve conter as seções `SOBRE O PRODUTO` e `BENEFÍCIOS` de forma flexível (validação case-insensitive e tolerante a markdown/acentos).
- **Resiliência e Fallback:** Se a descrição gerada/fallback tiver excelente qualidade (tamanho > 150 caracteres) mas faltarem os termos exatos de cabeçalho, o sistema emite um warning no log e permite a criação do kit prosseguir normalmente, evitando abortos rígidos catastróficos que travariam a operação por causa de formatação de texto.

Retorna mensagem de erro se a validação falhar completamente, ou `None` se estiver OK.

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

#### `executar_rollback_grade(sku_origem) -> Dict`
Realiza o desfazimento completo (rollback) de um lote inteiro de 8 variações a partir do `sku_origem` (base).
1. Obtém do histórico local todos os kits associados ao SKU de origem.
2. Faz chamadas à API do BaseLinker (`deleteInventoryProduct`) para deletar cada anúncio fisicamente do catálogo externo.
3. Remove os registros locais do banco de dados correspondente à criação (`HistoricoKit`).
4. Remove os GTINs/EANs gerados no banco local (`GtinKit`) para aquele SKU de origem.
5. Adiciona os SKUs deletados à tabela `NumeroReciclado` para que sejam reaproveitados em novas tentativas, evitando lacunas e mantendo a consistência.

#### `executar_exclusao_individual(id_kit, sku_kit) -> Dict`
Permite a exclusão isolada de uma única variação (anúncio individual) criada incorretamente.
1. Executa a deleção física do anúncio no BaseLinker via `deleteInventoryProduct`.
2. Remove o registro correspondente no histórico (`HistoricoKit`).
3. Remove o GTIN associado a esse SKU de kit final do banco de dados local (`GtinKit`).
4. Recicla o SKU inserindo-o em `NumeroReciclado` para reaproveitamento posterior.

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
    f. Determinar tag principal (classico/premium) e injetar tag "Marketplace" se Qtd == 1 e for modalidade Clássico
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

### Ação `rollback`
1. Recebe o SKU de origem a ser desfeito.
2. Chama `servico.executar_rollback_grade(sku_rollback)`.
3. Exibe feedback de sucesso ou falha no painel e recicla os SKUs e GTINs locais.

### Ação `excluir_individual`
1. Recebe os parâmetros `id_kit` (BaseLinker Product ID) e `sku_kit` (SKU do kit).
2. Chama `servico.executar_exclusao_individual(id_kit, sku_kit)`.
3. Limpa o histórico e o GTIN local, recicla o SKU e remove do catálogo do BaseLinker, exibindo mensagem de status.

### Ação `cancelar_lote`
1. Recebe o SKU de origem (`sku_origem`) via requisição assíncrona AJAX.
2. Salva a flag `"cancelado"` no cache do Django (`criacao_lote_{sku_origem}`) com validade de 120 segundos.
3. Permite a interrupção instantânea do loop de criação de grade e executa a limpeza completa via `executar_rollback_grade(sku_origem)`, incluindo a inativação imediata dos GTINs gerados até o momento diretamente na API da GS1 Brasil.

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
- Quantidade 1 → Usa o EAN legítimo do produto base. Se o produto base não possuir EAN cadastrado ou se estiver como `'PENDENTE'`, o sistema ativa automaticamente a autogeração resiliente (mesmo com a flag `gerar_ean_unitario` desligada) para garantir que o unitário vá ao ar com um EAN válido na GS1, ou se `gerar_ean_unitario` for `True` explicitamente.
- Quantidade > 1 → Gera um novo EAN oficial na GS1, compartilhado rigorosamente entre as variações Clássica e Premium de mesmo nível (quantidade) através da busca inteligente por quantidade no banco de dados local. Isso limita a exatamente 4 EANs gerados na GS1 por lote completo de 8 anúncios.

**Tags Especiais:**
- Quantidade 1 (apenas na modalidade Clássico) → Recebe adicionalmente a tag `Marketplace` no BaseLinker para distinção visual.

**Peso por variação:** `peso_base × quantidade` (+ peso_secundario × quantidade se Kit Misto)

---

## Dependências Externas

| Integração | Uso |
|---|---|
| `BaselinkerClient` | CRUD de produtos no inventário |
| `MercadoLivreClient` | Busca de categorias e atributos obrigatórios |
| `GS1Client` | Registro de GTINs/EANs oficiais |
| `GeminiClient` | Agentes de Compliance, Título e Descrição |
| `GroqClient` | Agentes de Ficha Técnica e Keywords |
| `Calculadora` (R831 v2) | Cálculo de preços (promoção + vitrine) |
| `TermosProibidos` | Validação de palavras proibidas no título |

---

## Interrupção em Tempo Real e Inativação na GS1

### Visão Geral do Fluxo de Cancelamento

Durante o processo de geração da grade de 8 anúncios (que leva em torno de 30 segundos devido às chamadas externas da API e IA), o usuário pode perceber a configuração de um produto incorreto e solicitar a interrupção imediata do processo. O sistema foi desenvolvido com suporte a cancelamento concorrente assíncrono em tempo real.

```mermaid
sequenceDiagram
    actor Usuario as Usuário (Painel Web)
    participant JS as Frontend (JavaScript)
    participant View as Django View (cancelar_lote)
    participant Cache as Django Cache
    participant Service as CriadorKitsService (executar_criacao_grade)
    participant BL as API BaseLinker
    participant GS1 as API GS1 Brasil (CNP)

    Usuario->>JS: Clica em "Parar Criação e Excluir GTINs"
    Note over JS: Extrai SKU de origem (sku_manual)<br/>e valida intenção
    JS->>View: Envia requisição AJAX POST (acao=cancelar_lote)
    View->>Cache: Salva criacao_lote_{sku_origem} = "cancelado"
    View-->>JS: Retorna confirmação de interrupção
    
    Note over Service: No início de cada iteração da grade
    Service->>Cache: Consulta criacao_lote_{sku_origem}
    alt Status é "cancelado"
        Note over Service: Aborta criação dos anúncios seguintes
        Service->>BL: Deleta anúncios do lote já cadastrados (deleteInventoryProduct)
        Service->>GS1: Inativa/Exclui EANs gerados no lote (inativar_ou_excluir_produto)
        Note over GS1: Tenta DELETE físico. Fallback para PATCH INACTIVE
        Note over Service: Recicla todos os SKUs no banco de dados local
        Service-->>Usuario: Feedback visual de sucesso do rollback e cancelamento
    else Status é "executando"
        Note over Service: Prossegue criando o anúncio atual
    end
```

### Detalhes Técnicos

1. **Overlay de Progresso e Comunicação AJAX:** O template `painel_kits.html` exibe um overlay em tela cheia com um spinner e um botão de interrupção. Quando o botão é clicado, o JS captura de forma precisa o SKU de origem do formulário (obtido através do input `sku_manual`) e envia via `fetch` assíncrono para a view do Django com `acao=cancelar_lote`.
2. **Consistência Atômica de GTINs:** O backend, ao detectar a flag `"cancelado"` no cache do Django no início de qualquer iteração de anúncio do lote, interrompe imediatamente o loop e chama o método `executar_rollback_grade(sku_origem)`. 
3. **Limpeza na GS1 Brasil:** O rollback lê os GTINs cadastrados localmente para aquele lote de criação e dispara requisições de exclusão/inativação na API oficial da GS1 Brasil (CNP) para cada um dos EANs recém-gerados, eliminando desperdício e impedindo a publicação de GTINs inválidos gerados a partir de produtos incorretos.
