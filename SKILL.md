---
name: so-produto-django
description: Padrões obrigatórios de arquitetura, segurança e código limpo para o projeto "Só Produto" em Django com múltiplas APIs. Use esta skill SEMPRE que for solicitado a criar, refatorar ou revisar qualquer código Python/Django no projeto — incluindo models, views, services, serializers, integrações com APIs externas, migrations, testes ou qualquer outro arquivo do sistema. Nunca escreva código para este projeto sem consultar esta skill antes.
---

# Só Produto — Padrões Obrigatórios de Desenvolvimento Django

Este documento define as regras que DEVEM ser seguidas em todo código gerado ou refatorado para o projeto "Só Produto". Todas as 9 regras são obrigatórias. Nenhuma pode ser ignorada.

---

## Regra 1 — Separação de Responsabilidades (Service Layer)

É **proibido** colocar lógica de negócio, cálculos, regras de domínio ou integrações de API dentro de Views ou diretamente nos Models.

| Camada | Responsabilidade | Proibido |
|---|---|---|
| `models.py` | Estrutura de dados, managers, propriedades simples | Lógica de negócio, chamadas a API |
| `services.py` | Toda a inteligência do sistema: cálculos, orquestrações, chamadas externas | Manipulação direta de request/response |
| `views.py` | Receber requisição, chamar o serviço, retornar resposta | Qualquer lógica além de receber e responder |

Estrutura de diretórios obrigatória por app:

```
meu_app/
├── models.py
├── services.py
├── views.py
└── urls.py
```

---

## Regra 2 — Zero Hardcode de Credenciais e Segredos

Sob **nenhuma circunstância** escreva tokens, senhas, chaves de API, URLs de produção ou qualquer dado sensível diretamente no código-fonte.

- Use **sempre** `config('NOME_DA_VARIAVEL')` da biblioteca `decouple`.
- Ao final de toda resposta que introduza novas variáveis sensíveis, liste-as obrigatoriamente no formato:

```
# Adicionar ao .env:
NOME_DA_VARIAVEL=valor_exemplo
```

- Dados sensíveis que trafegam entre APIs (tokens, CPFs, dados financeiros) devem ser mascarados nos logs com `***` ou `[REDACTED]` antes de registrar.
- Tokens de autenticação entre serviços devem ser passados **sempre via headers HTTP**, nunca via query string.

---

## Regra 3 — Decomposição de Métodos (Responsabilidade Única)

Todo método ou função deve fazer **uma única coisa**. Se um método precisar de mais de um nível de abstração para ser descrito, ele deve ser dividido.

- Métodos **não devem ultrapassar 20 linhas** de código. Se ultrapassar, divida.
- Sequências de passos de negócio devem ser orquestradas por um método "coordenador" que chama sub-métodos em ordem.
- O nome do método deve descrever exatamente o que ele faz, sem precisar de comentários inline.

Exemplo de estrutura correta:

```python
def processar_pedido(self, dados: dict) -> dict:
    """Orquestra o processamento completo de um pedido."""
    pedido = self._criar_pedido(dados)
    self._aplicar_desconto(pedido)
    self._notificar_cliente(pedido)
    return self._serializar_resposta(pedido)

def _criar_pedido(self, dados: dict) -> Pedido:
    """Cria e persiste um novo pedido a partir dos dados validados."""
    ...

def _aplicar_desconto(self, pedido: Pedido) -> None:
    """Aplica regras de desconto ao pedido conforme a política vigente."""
    ...

def _notificar_cliente(self, pedido: Pedido) -> None:
    """Envia a notificação de confirmação ao cliente do pedido."""
    ...

def _serializar_resposta(self, pedido: Pedido) -> dict:
    """Retorna o dicionário de resposta padronizado para o pedido."""
    ...
```

---

## Regra 4 — Privacidade de Métodos Internos

Métodos auxiliares que não devem ser acessados externamente **obrigatoriamente começam com underscore**.

```python
def _calcular_imposto(self, valor: Decimal) -> Decimal:
    """Calcula o imposto sobre o valor bruto do produto."""
    ...
```

Critério: se o método é chamado apenas dentro da própria classe ou serviço, ele é privado e deve ter `_` no início.

---

## Regra 5 — Tipagem Estrita e Docstrings

Todo novo método ou função deve obrigatoriamente:

1. Incluir **Type Hints** em todos os parâmetros e no retorno.
2. Incluir uma **Docstring concisa** na primeira linha descrevendo o que o método faz (não como).

```python
def buscar_produto(self, produto_id: int) -> dict:
    """Retorna os dados de um produto pelo seu ID."""
    ...

def _validar_estoque(self, quantidade: int, disponivel: int) -> bool:
    """Verifica se a quantidade solicitada está disponível em estoque."""
    ...
```

Tipos aceitos: `str`, `int`, `float`, `bool`, `dict`, `list`, `Decimal`, `Optional[X]`, `QuerySet`, `None`, ou tipos customizados do projeto.

---

## Regra 6 — Tratamento de Erros e Rastreabilidade

**Nunca** crie blocos `try/except` silenciosos com `pass`.

Todo erro capturado deve ser obrigatoriamente registrado com `logging`:

```python
import logging
logger = logging.getLogger(__name__)

try:
    resultado = api_externa.chamar()
except MinhaExcecaoEspecifica as e:
    logger.error("Falha ao chamar API externa | contexto: %s | erro: %s", contexto, str(e), exc_info=True)
    raise
```

- Capture sempre **exceções específicas**. Nunca use `except Exception` genérico sem justificativa explícita no comentário.
- Use `logger.error(...)` para falhas operacionais e `logger.warning(...)` para situações inesperadas mas recuperáveis.
- Inclua contexto relevante na mensagem do log (ID do objeto, nome da API, operação em execução).

---

## Regra 7 — Integrações com APIs Externas

Toda chamada a uma API externa deve obrigatoriamente:

1. Ter **timeout configurado** via variável de ambiente.
2. Usar **retry com backoff exponencial** para falhas transitórias com a biblioteca `tenacity`.
3. Ser encapsulada em seu próprio método de serviço com tratamento de erro explícito.
4. **Nunca logar** o body completo de requisições ou respostas que contenham tokens, senhas ou dados pessoais.

Estrutura de referência:

```python
from tenacity import retry, stop_after_attempt, wait_exponential
from decouple import config
import logging

logger = logging.getLogger(__name__)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _chamar_api_pagamento(self, payload: dict) -> dict:
    """Realiza a chamada à API de pagamento com retry automático."""
    url = config('PAGAMENTO_API_URL')
    timeout = config('PAGAMENTO_API_TIMEOUT', cast=int, default=10)
    headers = {"Authorization": f"Bearer {config('PAGAMENTO_API_TOKEN')}"}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.Timeout as e:
        logger.error("Timeout ao chamar API de pagamento | timeout: %ss | erro: %s", timeout, str(e))
        raise
    except requests.exceptions.HTTPError as e:
        logger.error("Erro HTTP na API de pagamento | status: %s | erro: %s", e.response.status_code, str(e))
        raise
```

---

## Regra 8 — Segurança de Dados em Trânsito e Migrações

- **Migrações:** nunca inclua dados sensíveis (tokens, senhas, chaves) em arquivos de migração. Dados de seed sensíveis devem usar `management commands` que leem do `.env`.
- **Logs:** nunca registre senhas, tokens, números de cartão ou CPFs em texto puro. Mascare com `[REDACTED]` ou `***`.
- **Autenticação entre serviços:** tokens devem ser lidos via `config()` e passados **sempre via headers HTTP**, nunca via query string.
- **LGPD:** todo campo que contenha dado pessoal identificável deve ter o comentário `# PII` ao lado na declaração do Model.

```python
class Cliente(models.Model):
    nome = models.CharField(max_length=255)          # PII
    cpf = models.CharField(max_length=14, unique=True)  # PII
    email = models.EmailField()                       # PII
    criado_em = models.DateTimeField(auto_now_add=True)
```

---

## Regra 9 — Formato Obrigatório de Resposta

Toda resposta que gere ou refatore código **deve seguir esta estrutura**:

1. **Arquivos afetados:** liste quais arquivos serão criados ou modificados.
2. **Código completo:** com todos os imports necessários, Type Hints e Docstrings.
3. **Variáveis de ambiente:** se aplicável, liste no rodapé quais entradas devem ser adicionadas ao `.env`.
4. **Alertas de segurança:** se houver algum ponto de atenção de segurança ou privacidade, destaque explicitamente.

Rodapé obrigatório quando houver novas variáveis:

```
---
📌 Adicionar ao .env:
NOME_VARIAVEL=valor_exemplo
OUTRO_SEGREDO=valor_exemplo
```
