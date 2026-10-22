# DOCUMENTAÇÃO TÉCNICA
## Estratégia de GTIN e Estrutura de Anúncios
### Mercado Livre – Implementação Django

**Versão 1.0** | **Classificação:** Interno | **Time TI Junior**

> ⚠️ **IMPORTANTE:** Este documento define regras **CRÍTICAS** de negócio. Leia cada seção por completo antes de implementar. Qualquer desvio pode gerar bloqueios na plataforma Mercado Livre.

---

## 1. Visão Geral e Objetivo

Este documento define de forma executável as regras obrigatórias para o time de TI junior implementar o módulo de criação de anúncios no Mercado Livre utilizando o framework Django.

### 1.1 O que este módulo faz

- Recebe dados de produto (nome, marca, quantidade, GTIN do fornecedor)
- Define automaticamente o GTIN correto (unitário ou kit)
- Gera os SKUs padronizados
- Cria dois anúncios por variante: Clássico e Premium
- Valida todas as regras antes de salvar no banco
- Envia os anúncios para a API do Mercado Livre

### 1.2 Por que isso é crítico

Erros neste módulo causam, diretamente:

- Bloqueio de anúncios na plataforma
- Duplicidade de catálogo (penalidade grave no ML)
- Agrupamento incorreto de variantes
- Erros de indexação e perda de visibilidade

> 🔴 **REGRA FUNDAMENTAL:** GTIN identifica o **PRODUTO FÍSICO**. SKU identifica o **ANÚNCIO**. Nunca confundir esses dois conceitos.

---

## 2. Conceitos Fundamentais

### 2.1 O que é GTIN

GTIN (Global Trade Item Number) é o código que identifica um produto físico no mercado. É o mesmo que EAN ou código de barras. No contexto deste sistema:

- **Produto unitário:** usa o GTIN fornecido pelo fabricante/fornecedor
- **Kit (2+ unidades):** OBRIGATÓRIO gerar um GTIN próprio e único
- Clássico e Premium do **MESMO** produto compartilham o **MESMO GTIN**

### 2.2 O que é SKU

SKU (Stock Keeping Unit) é o código interno que diferencia cada anúncio. No sistema, o SKU segue o padrão:

```
[CODIGO_PRODUTO]-[QUANTIDADE]-[TIPO]
```

Onde TIPO pode ser:

- `CL` = Clássico
- `PR` = Premium

### 2.3 Tipos de Anúncio no Mercado Livre

| Tipo | Características | Uso recomendado |
|------|----------------|-----------------|
| **Clássico (CL)** | Taxa menor, menos visibilidade, sem garantia obrigatória | Produtos de menor giro |
| **Premium (PR)** | Taxa maior, mais visibilidade, frete grátis obrigatório | Produtos de alto giro |

---

## 3. Regras de GTIN – LEIA COM ATENÇÃO

### 3.1 Produto Unitário

Usar o GTIN exatamente como recebido do fornecedor. Criar dois anúncios (Clássico e Premium) com o **MESMO GTIN**.

### 3.2 Kits (2 ou mais unidades)

É **OBRIGATÓRIO** gerar um GTIN novo e exclusivo para cada quantidade de kit diferente.

- Kit de 2 unidades → 1 GTIN próprio
- Kit de 3 unidades → outro GTIN próprio (diferente do kit 2)
- Kit de 5 unidades → outro GTIN próprio (diferente dos anteriores)

> ⚠️ **ATENÇÃO:** Cada quantidade de kit é um produto diferente no catálogo. Não existe 'reaproveitar' GTIN entre quantidades.

### 3.3 Tabela de Bloqueios – Situações Proibidas

| Situação Proibida | Consequência |
|-------------------|--------------|
| Kit usando GTIN do fornecedor | **BLOQUEIO IMEDIATO** no Mercado Livre |
| GTIN ausente ou em branco | **BLOQUEIO IMEDIATO** no Mercado Livre |
| Mesmo GTIN para kits de quantidades diferentes | Duplicidade de catálogo |
| GTIN diferente para CL e PR do mesmo produto | Agrupamento incorreto |
| GTIN de kit não persistido no banco | Inconsistência a cada geração |

---

## 4. Estrutura de SKU

### 4.1 Padrão obrigatório

```
SHA-A-1-CL
^     ^  ^  ^
Produto  Qtd  Tipo
```

### 4.2 Exemplos de SKU

| Produto | Quantidade | Tipo | SKU resultante |
|---------|-----------|------|----------------|
| Shampoo A | 1 | Clássico | `SHA-A-1-CL` |
| Shampoo A | 1 | Premium | `SHA-A-1-PR` |
| Shampoo A | 2 | Clássico | `SHA-A-2-CL` |
| Shampoo A | 2 | Premium | `SHA-A-2-PR` |
| Shampoo A | 3 | Clássico | `SHA-A-3-CL` |
| Shampoo A | 3 | Premium | `SHA-A-3-PR` |

### 4.3 Regras de unicidade do SKU

- SKU deve ser **ÚNICO** no banco de dados (`unique=True` no model)
- Não pode existir dois anúncios com o mesmo SKU
- O SKU **não muda** após a criação – é imutável

---

## 5. Exemplo Prático Completo

**Produto:** Shampoo A | **GTIN do fornecedor:** `00000000879F`

### 5.1 Unitário

| Tipo | SKU | GTIN |
|------|-----|------|
| Clássico | `SHA-A-1-CL` | `00000000879F` |
| Premium | `SHA-A-1-PR` | `00000000879F` |

### 5.2 Kit 2 unidades

> GTIN de kit é gerado pelo sistema, não é o do fornecedor. Ex. fictício: `10000000000021`

| Tipo | SKU | GTIN |
|------|-----|------|
| Clássico | `SHA-A-2-CL` | `10000000000021` |
| Premium | `SHA-A-2-PR` | `10000000000021` |

### 5.3 Kit 3 unidades

> GTIN diferente do kit 2. Ex. fictício: `10000000000038`

| Tipo | SKU | GTIN |
|------|-----|------|
| Clássico | `SHA-A-3-CL` | `10000000000038` |
| Premium | `SHA-A-3-PR` | `10000000000038` |

---

## 6. Modelagem Django – Models

### 6.1 ProdutoBase

Armazena o produto original com o GTIN do fornecedor.

```python
# models.py

class ProdutoBase(models.Model):
    nome = models.CharField(max_length=255)
    marca = models.CharField(max_length=255)
    gtin_fornecedor = models.CharField(max_length=20)

    def __str__(self):
        return f'{self.marca} - {self.nome}'
```

### 6.2 ProdutoVariante

Representa cada variação de quantidade (unitário, kit 2, kit 3, etc.). Cada variante tem seu GTIN próprio.

```python
class ProdutoVariante(models.Model):
    produto = models.ForeignKey(
        ProdutoBase,
        on_delete=models.CASCADE,
        related_name='variantes'
    )
    quantidade = models.IntegerField()
    gtin = models.CharField(max_length=20, unique=True)

    class Meta:
        unique_together = [['produto', 'quantidade']]

    def __str__(self):
        return f'{self.produto} - Qtd {self.quantidade}'
```

### 6.3 Anuncio

Cada variante gera dois anúncios: CL (Clássico) e PR (Premium).

```python
class Anuncio(models.Model):
    TIPO_CHOICES = [
        ('CL', 'Classico'),
        ('PR', 'Premium'),
    ]

    variante = models.ForeignKey(
        ProdutoVariante,
        on_delete=models.CASCADE,
        related_name='anuncios'
    )
    tipo = models.CharField(max_length=2, choices=TIPO_CHOICES)
    sku = models.CharField(max_length=50, unique=True)
    ml_id = models.CharField(max_length=50, blank=True)  # ID no Mercado Livre
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [['variante', 'tipo']]
```

> ⚠️ **ATENÇÃO:** O campo `gtin` **NUNCA** deve ser igual ao `gtin_fornecedor` quando `quantidade > 1`. Esta regra é validada na camada de serviço (ver Seção 8).

---

## 7. Geração de GTIN para Kits

### 7.1 Requisitos obrigatórios

- GTIN de kit deve ser **ÚNICO** por produto+quantidade
- Deve ser **PERSISTIDO** no banco na primeira geração
- **Não** pode ser regenerado aleatoriamente a cada chamada
- Em produção: deve ser um GTIN GS1 oficial

### 7.2 Implementação – Fase de Desenvolvimento (Mock)

> Use esta função apenas para desenvolvimento e testes. Em produção, substitua pela integração GS1.

```python
# services/gtin_service.py

import uuid

def gerar_gtin_kit(produto_id: int, quantidade: int) -> str:
    """
    Gera GTIN determinístico para kit.
    IMPORTANTE: Em produção substituir por GTIN GS1 oficial.
    """
    # Verifica se já existe GTIN para este produto+quantidade
    from .models import ProdutoVariante

    existente = ProdutoVariante.objects.filter(
        produto_id=produto_id,
        quantidade=quantidade
    ).first()

    if existente:
        return existente.gtin  # Retorna o já persistido

    # Gera novo GTIN mock (determinístico via hash)
    seed = f'kit-{produto_id}-{quantidade}'
    hash_val = int(uuid.uuid5(uuid.NAMESPACE_DNS, seed).int % 9999999999999)
    return f'1{hash_val:013d}'  # Prefixo 1 + 13 dígitos
```

> 🔔 **EM PRODUÇÃO:** Substituir por chamada à API GS1 Brasil ou gerador de EAN-13 válido com dígito verificador correto.

---

## 8. Serviço Principal – AnuncioService

### 8.1 Entrada esperada

```python
payload = {
    'produto': 'Shampoo A',
    'marca': 'Marca X',
    'quantidade': 2,
    'gtin_fornecedor': '00000000879F'
}
```

### 8.2 Implementação completa

```python
# services/anuncio_service.py

from django.db import transaction
from .models import ProdutoBase, ProdutoVariante, Anuncio
from .gtin_service import gerar_gtin_kit

def criar_anuncios(payload: dict) -> list:
    """
    Cria variante + 2 anúncios (CL e PR) para um produto.
    Retorna lista de Anuncio criados.
    """
    nome = payload['produto']
    marca = payload['marca']
    quantidade = payload['quantidade']
    gtin_fornecedor = payload['gtin_fornecedor']

    with transaction.atomic():
        # 1. Obter ou criar produto base
        produto, _ = ProdutoBase.objects.get_or_create(
            nome=nome, marca=marca,
            defaults={'gtin_fornecedor': gtin_fornecedor}
        )

        # 2. Definir GTIN correto
        if quantidade == 1:
            gtin = gtin_fornecedor
        else:
            gtin = gerar_gtin_kit(produto.id, quantidade)

        # 3. Validar regras críticas
        _validar_gtin(gtin, gtin_fornecedor, quantidade)

        # 4. Criar variante
        variante, _ = ProdutoVariante.objects.get_or_create(
            produto=produto, quantidade=quantidade,
            defaults={'gtin': gtin}
        )

        # 5. Gerar SKU e criar anúncios
        codigo_produto = _gerar_codigo_produto(nome)
        anuncios = []

        for tipo in ['CL', 'PR']:
            sku = f'{codigo_produto}-{quantidade}-{tipo}'
            anuncio, created = Anuncio.objects.get_or_create(
                variante=variante, tipo=tipo,
                defaults={'sku': sku}
            )
            anuncios.append(anuncio)

        return anuncios
```

### 8.3 Funções auxiliares

```python
def _validar_gtin(gtin: str, gtin_fornecedor: str, quantidade: int):
    """Valida regras críticas de GTIN. Lança ValueError se inválido."""
    if not gtin:
        raise ValueError('GTIN não pode ser vazio.')

    if quantidade > 1 and gtin == gtin_fornecedor:
        raise ValueError(
            f'Kit (qtd={quantidade}) NÃO pode usar o GTIN do fornecedor.'
        )

def _gerar_codigo_produto(nome: str) -> str:
    """Gera código curto do produto a partir do nome."""
    palavras = nome.upper().split()
    return '-'.join(p[:3] for p in palavras[:2])
```

---

## 9. Validações Obrigatórias

### 9.1 Validações no Model (Django)

```python
from django.core.exceptions import ValidationError

class ProdutoVariante(models.Model):
    # ... campos ...

    def clean(self):
        super().clean()
        if self.quantidade > 1:
            if self.gtin == self.produto.gtin_fornecedor:
                raise ValidationError(
                    'Kit não pode usar GTIN do fornecedor.'
                )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)
```

### 9.2 Checklist completo de validação

| Regra | Onde validar | Ação se falhar |
|-------|-------------|----------------|
| GTIN não pode ser vazio | Service + Model | `raise ValueError` |
| Kit não usa GTIN do fornecedor | Service + Model | `raise ValueError` |
| SKU deve ser único | Model (`unique=True`) | `IntegrityError` |
| GTIN deve ser único por variante | Model (`unique=True`) | `IntegrityError` |
| Produto+Quantidade único | Model (`unique_together`) | `IntegrityError` |
| GTIN de kit persistido antes de usar | Service (`get_or_create`) | Automático no fluxo |

---

## 10. Fluxo Completo do Sistema

### 10.1 Passo a passo de execução

1. Receber payload com produto, marca, quantidade e `gtin_fornecedor`
2. Verificar se produto já existe (`get_or_create` em `ProdutoBase`)
3. Verificar se é unitário (`qtd=1`) ou kit (`qtd>1`)
4. **Unitário:** usar `gtin_fornecedor` diretamente
5. **Kit:** chamar `gerar_gtin_kit()` – verifica BD antes de gerar novo
6. Executar `_validar_gtin()` – bloqueia se regra violada
7. Criar `ProdutoVariante` com gtin definido (`get_or_create`)
8. Gerar SKU para CL: `{codigo}-{qtd}-CL`
9. Gerar SKU para PR: `{codigo}-{qtd}-PR`
10. Criar Anuncio CL (`get_or_create`)
11. Criar Anuncio PR (`get_or_create`)
12. Retornar lista de anúncios criados
13. Enviar para API do Mercado Livre

### 10.2 Comportamento com `get_or_create`

O uso de `get_or_create` é **intencional e obrigatório**. Garante idempotência: rodar o mesmo payload duas vezes não duplica dados.

- Se variante já existe → retorna a existente, não cria nova
- Se anúncio já existe → retorna o existente, não duplica
- O GTIN de kit gerado é o mesmo já persistido – nunca regenera

---

## 11. Erros Comuns – Guia de Prevenção

| Erro | Causa | Correção |
|------|-------|----------|
| **Kit com GTIN do fornecedor** | Não verificou quantidade antes de atribuir GTIN | Usar condicional `if quantidade == 1` |
| **CL e PR com GTINs diferentes** | Atribuiu GTIN por anúncio em vez de por variante | GTIN fica na variante, não no anúncio |
| **GTIN de kit muda a cada execução** | Gerando GTIN sem persistir | Sempre usar `get_or_create` antes de gerar |
| **SKU duplicado** | Não configurou `unique=True` | Adicionar `unique=True` no campo SKU |
| **Confundir SKU com GTIN** | Conceitos misturados na implementação | Reler Seções 2 e 4 deste documento |
| **Kit sem GTIN próprio** | Reutilizou GTIN de outro kit | Cada quantidade tem seu próprio GTIN |

---

## 12. Resumo Rápido – Card de Referência

| Regra | Comportamento |
|-------|--------------|
| Produto unitário | GTIN = `gtin_fornecedor` |
| Kit qualquer quantidade | GTIN = `gerar_gtin_kit()` – novo e único |
| Anúncio Clássico e Premium | Mesmo GTIN, SKU diferente (`CL` / `PR`) |
| SKU | Formato: `CODIGO-QUANTIDADE-TIPO` |
| GTIN de kit | Persistir no banco, nunca regenerar |
| Validação | Service + Model `clean()` |
| Idempotência | Sempre usar `get_or_create` |

---

## 13. Variáveis de Ambiente e Resiliência

### 13.1 Variáveis de Ambiente (.env)
- `GS1_API_TIMEOUT` (int, default: 30): Tempo limite em segundos para chamadas de requisição HTTP na API GS1.

### 13.2 Mecanismo de Retry (Tenacity)
- Toda chamada externa POST para a GS1 está equipada com a política de retry automático utilizando `tenacity` com **backoff exponencial** (mínimo de 4 segundos, máximo de 15 segundos, e limite de 3 tentativas). Erros transitórios HTTP 5xx (500, 502, 503, 504) e o erro HTTP 403 (Access Denied / Rate Limit do Akamai) disparam retries automáticos.
- **Atualização em 17/06/2026:** Para mitigar erros de autenticação ou expiração prematura no servidor GS1, quando um erro HTTP 403 é detectado, o sistema limpa o token de acesso cacheado localmente e obtém um novo token, atualizando os cabeçalhos da sessão antes do início do retry.

---

## 14. Pacote Exportado Autônomo (Standalone Exporter)

Exportado em `exports/export_gs1/`, permitindo o funcionamento independente do framework Django:

- `client_gs1.py`: Cliente HTTP da API GS1 Brasil (CNP API v2/v3).
- `client_baselinker.py`: Cliente HTTP da API do BaseLinker.
- `gerador_ean.py`: Orquestrador de serviços e CLI (`python gerador_ean.py --sku SKU --gpc GPC`).
- `web_app.py`: Servidor HTTP autônomo (`python web_app.py`).
- `templates/gerar_ean.html`: Interface web responsiva com glassmorphism, suporte a cópia de GTIN e tratamentos visuais.

### 14.1 Resolução de Marca / Fabricante no BaseLinker (Atualização de 25/09/2026)
- **Suporte a `manufacturer_id`:** O cliente `BaselinkerClient` resolve o ID do fabricante via chamada `getInventoryManufacturers` (cacheado localmente), convertendo `manufacturer_id` no nome textual da marca selecionada no painel do BaseLinker.
- **Normalização de Features:** Suporta estruturas de features presentes na raiz (`dados["features"]`) e em `text_fields["features"]`, cobrindo dicionários e listas chave/valor para marcas e NCMs.

---

> 💬 **Dúvidas?** Consulte o líder técnico antes de implementar qualquer variação das regras acima. Este documento é a fonte da verdade para o módulo de anúncios.


