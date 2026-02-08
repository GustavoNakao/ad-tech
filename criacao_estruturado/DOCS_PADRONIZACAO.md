# 📄 Especificação Técnica: Automação de Kits "Estruturado"

**Versão:** 3.0 (Fluxo Completo)
**Data:** 14/12/2025
**App:** `criacao_estruturado`

---

## 1. Fluxo Operacional (Macro) 🔄

1.  **Entrada (Físico):** Produto chega do fornecedor.
2.  **Bling (Fiscal/Estoque):**
    * Nota Fiscal é dada baixa.
    * Produto é criado manualmente no Bling com categoria **"Estruturado"**.
    * Estoque físico é lançado no Bling.
3.  **Sincronização (Intermediário):**
    * Script "Colab" roda e envia o produto para o BaseLinker.
    * Produto chega no BaseLinker (Aba Produtos) com SKU do Bling (ex: `30567`) e Categoria "Estruturado".
4.  **Automação Django (Nosso Robô):**
    * **Leitura:** O sistema detecta o novo produto "30567" na categoria Estruturado.
    * **Clonagem:** Cria um NOVO produto (Kit) copiando 100% dos dados (Nome, Fotos, Descrição, EAN, Dimensões).
    * **Sequenciamento:** O novo Kit recebe o próximo SKU da empresa (ex: `10635`).
    * **Vínculo:** O novo Kit é configurado como "Produto Composto" (Bundle) contendo 1 unidade do SKU `30567`.

---

## 2. Regras de Automação (O Robô) 🤖

* **Gatilho:** Existência de produto na categoria "Estruturado" que ainda não possui registro na tabela `HistoricoKitEstruturado`.
* **Ação:** Clonar 1:1 e transformar em Bundle.
* **Estoque:** O Kit `10635` **não** tem estoque próprio. Ele consome o estoque do `30567`.
* **Segurança:** Se o produto já foi processado, o robô ignora para não criar duplicidade.

---

## 3. Estrutura de Banco de Dados

### Tabela `ControleSequencia`
*Guarda onde parou a numeração dos Kits.*
* **Exemplo:** `proximo_numero = 10635`

### Tabela `HistoricoKitEstruturado`
*Guarda o vínculo para evitar repetição.*
* **Exemplo:** `sku_origem_bling="30567"` vinculado a `sku_kit_criado="10635"`.