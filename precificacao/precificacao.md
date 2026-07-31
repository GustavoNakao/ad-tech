# Motor de Precificação R831 — v4.3 (Protocolo Greenfield)
> Documentação Técnica · Consolidada e Revisada

| | |
|---|---|
| **Versão** | 4.3 |
| **Modalidade** | Mercado Livre Brasil |
| **Plataforma** | BaseLinker + Neon DB |
| **Integração** | API Mercado Livre |

---

## Sumário

1. [Entradas de Dados](#1-entradas-de-dados)
2. [Pilares do Cálculo — O Denominador de Blindagem](#2-pilares-do-cálculo--o-denominador-de-blindagem)
3. [Matriz Logística (Mercado Envios Oficial)](#3-matriz-logística-mercado-envios-oficial)
4. [Tabela de Comissões por Modalidade](#4-tabela-de-comissões-por-modalidade)
5. [Regra de Ouro: Tarifa Reduzida (R$ 150 — R$ 700)](#5-regra-de-ouro-tarifa-reduzida-r-150--r-700)
6. [Estratégia de Saída — Grade de 8 Anúncios](#6-estratégia-de-saída--grade-de-8-anúncios)
7. [Parâmetros Globais e Configuração do Sistema](#7-parâmetros-globais-e-configuração-do-sistema)
8. [Tratamento de Erros e Fallbacks](#8-tratamento-de-erros-e-fallbacks)
9. [Exemplo Numérico End-to-End](#9-exemplo-numérico-end-to-end)
10. [Schema de Retorno para a API do BaseLinker](#10-schema-de-retorno-para-a-api-do-baselinker)
11. [Fluxo de Atualização de Preço](#11-fluxo-de-atualização-de-preço)
12. [Protocolo de Sincronização: Versão 4.3 (Greenfield)](#12-protocolo-de-sincronização-versão-43-greenfield)

---

## 1. Entradas de Dados

A função recebe os seguintes campos extraídos do Neon DB e do BaseLinker. Todos são obrigatórios, exceto onde indicado.

| Campo | Origem | Tipo | Descrição |
|---|---|---|---|
| `average_cost` | Neon DB | DECIMAL | Custo líquido já deduzido créditos (R$) |
| `tax_rate` | Neon DB | ENUM | Imposto de saída: `0%`, `9,25%` ou `27,25%` |
| `weight` | BaseLinker | DECIMAL | Peso real do produto (kg) |
| `height / width / length` | BaseLinker | DECIMAL | Dimensões em cm para cálculo do peso cubado |
| `category_id` | BaseLinker | STRING | ID da categoria ML — determina comissão e elegibilidade fiscal |
| `custo_saida_transportadora` | Neon DB | DECIMAL | Custo manual de transportadora (obrigatório se peso > 30 kg) |

---

## 2. Pilares do Cálculo — O Denominador de Blindagem

O **Preço de Promoção** é o preço mínimo que garante a margem líquida de 12% após todos os descontos. Qualquer valor abaixo disso representa prejuízo. A fórmula usa a estrutura de denominador composto para blindar a margem:

```
Preço de Promoção = Numerador (R$) / (1 - Denominador (%))
Denominador     = Margem_Liquida + Imposto_Saida + Trava_Fiscal + Comissao_ML
Denominador (Greenfield) = 0.25 (Margem) + Imposto_Saida + 0.0 (Trava) + 0.165 (Comissão Safety First)
Numerador       = (Custo_Liq × Qtd_Kit) + Custo_Logistico + R$0,79 + R$1,50
```

### 2.1 Variáveis do Denominador (%)

| Variável | Valor | Observação |
|---|---|---|
| Margem Líquida (piso) | `12%` fixo | Lucro mínimo garantido em qualquer anúncio (12% em Patrimonio, 25% em Greenfield) |
| Imposto de Saída | Variável (`tax_rate`) | 0%, 9,25% ou 27,25% conforme regime fiscal |
| Trava Fiscal (TEF/DIFAL) | `4,5%` fixo | Reserva para obrigações fiscais estaduais (0% em Greenfield) |
| Comissão ML | Dinâmica via `category_id` | Ver [Seção 4](#4-tabela-de-comissões-por-modalidade) (16,5% fixo em Greenfield) |

### 2.2 Variáveis do Numerador (R$)

| Variável | Valor | Observação |
|---|---|---|
| Custo Produto | `average_cost × Qtd_Kit` | Multiplicado pelas unidades do kit |
| Custo Logístico | Calculado ([Seção 3](#3-matriz-logística-mercado-envios-oficial)) | Baseado em peso e faixa de preço |
| Custo BaseLinker | `R$ 0,79` fixo | Taxa por anúncio (Zera em Greenfield) |
| Custo GNRE/Operacional | `R$ 1,50` fixo | Custo operacional (Zera em Greenfield) |

---

## 3. Matriz Logística (Mercado Envios Oficial)

O custo logístico é determinado por três fatores: peso efetivo do produto, faixa de preço final do anúncio e reputação da conta vendedora.

### 3.1 Cálculo do Peso Efetivo

O sistema utiliza sempre o **maior valor** entre o peso real e o peso cubado:

```
Peso_Cubado  = (Height × Width × Length) / 6000   → resultado em kg
Peso_Efetivo = MAX(weight, Peso_Cubado)
```

### 3.2 Parâmetro de Reputação da Conta

Configurado globalmente em `tb_parametros_globais`, campo `reputacao_conta`. Define o desconto aplicado sobre a tabela oficial do Mercado Envios.

| Status | Desconto Frete | Condição |
|---|---|---|
| Verde / MercadoLíder | 50% | Desconto aplicado sobre tabela ML oficial |
| Outros níveis | 0% | Nenhum desconto — tabela ML integralmente |

### 3.3 Lógica de Frete por Faixa de Preço

| Condição de Preço | Custo Tabela ML | Taxa Fixa / Unidade Kit | Exceção Peso > 30 kg |
|---|---|---|---|
| Preço Final < R$ 79,00 | R$ 0,00 | R$ 6,50 por unidade | Usar `custo_saida_transportadora` |
| R$ 79,00 ≤ Preço < R$ 150,00 | Tabela ML por peso | R$ 0,00 | Usar `custo_saida_transportadora` |
| Preço ≥ R$ 150,00 | Tabela ML por peso | R$ 0,00 | Usar `custo_saida_transportadora` |

---

## 4. Tabela de Comissões por Modalidade

A comissão do Mercado Livre varia conforme a categoria do produto e a modalidade do anúncio (Clássico ou Premium).

| Categoria / Tipo | Clássico | Premium | Obs. |
|---|---|---|---|
| Geral (padrão) | 11,5% | 16% | Maioria das categorias |
| Tarifa Reduzida elegível | 9,5% | 14% | Apenas R$ 150–R$ 700 |
| Protocolo Greenfield | 16,5% | 16,5% | Blindagem de Tarifa (Safety First) |

---

## 5. Regra de Ouro: Tarifa Reduzida (R$ 150 — R$ 700)

1. Verificar se `category_id` possui `elegivel_tarifa_reduzida = true`.
2. Se elegível e **Preço de Promoção** entre R$ 150,00 e R$ 700,00 → recalcular com as comissões reduzidas.
3. **Trava de Segurança:** se o recálculo gerar preço abaixo de R$ 150,00 → forçar `Preço de Promoção = R$ 150,01`.

---

## 6. Estratégia de Saída — Grade de 8 Anúncios

O sistema gera automaticamente **8 registros de anúncio** (1 a 4 unidades × Clássico/Premium).

---

## 12. Protocolo de Sincronização: Versão 4.3 (Greenfield)

Este protocolo é aplicado automaticamente para todos os SKUs marcados como `protocolo_greenfield` ou que possuam **menos de 30 vendas nos últimos 60 dias**.

### 12.1 Regra 1: Blindagem de Tarifa (Safety First)
Para anúncios Greenfield, a tarifa padrão do Mercado Livre é fixada em **16,5% (0.165)** para o cálculo do Reverse Pricing, independentemente da modalidade (Clássico ou Premium).
- **Objetivo:** Evitar prejuízo oculto em anúncios Premium. Em anúncios Clássicos (11,5%), isso resulta em margem líquida excedente.

### 12.2 Regra 2: Reset de Custos Operacionais
As seguintes variáveis são zeradas no cálculo de Greenfield:
- `custo_gnre = 0.00`
- `custo_baselinker = 0.00`
- **Nota:** Para anúncios **Patrimônio** (>= 30 vendas), esses custos continuam ativos (R$ 1,50 e R$ 0,79).

### 12.3 Regra 3: Lógica de Hysteresis (O Salto dos R$ 79)
Sempre que o preço final (Vitrine) calculado cair na "zona morta", o sistema força o teto de **R$ 79,00** para capturar a isenção da taxa fixa de R$ 6,50 do Mercado Livre.
- **Zona Morta:** `72.00 <= Preço_Calculado <= 78.99` → Forçar `R$ 79,00`.
- **Tag de Auditoria:** `SALTO_79_LUCRO_MAX`.

### 12.4 Auditoria e Tags de Log
O sistema registra o protocolo aplicado no campo de log/comentário do banco de dados:
- `[R831-PATRIMONIO]` -> Regra padrão com todos os custos ativos.
- `[R831-GREENFIELD]` -> Regra de incentivo (Custos 0 e Margem Meta 25%).
- `[R831-GREENFIELD] - SALTO 79` -> Quando a lógica de Hysteresis (Isenção de R$ 6,50) foi acionada.

---

## 13. Atualizações e Ajustes Técnicos (Sprint 2)
### Atualizado em 2026-05-27
- **Fim dos Valores Hardcoded no Hysteresis (M7)**: Os limites e o teto da lógica de Hysteresis (anteriormente `72.00` e `79.00`) foram substituídos por referências dinâmicas aos parâmetros ativos salvos no banco de dados (`cfg.piso_frete_gratis` e calculando limites com base nele). Isso garante que mudanças no admin reflitam imediatamente no motor de cálculo Greenfield.
- **Constraint de Singleton em RegrasPrecificacao (M9)**: Foi adicionada a restrição `UniqueConstraint(fields=["ativo"], condition=Q(ativo=True), name="unico_regra_precificacao_ativa")` no modelo `RegrasPrecificacao` para garantir, a nível de banco de dados, que apenas um conjunto de regras possa estar ativo a qualquer momento.

---

*Fim da Documentação — Motor de Precificação R831 v4.3*