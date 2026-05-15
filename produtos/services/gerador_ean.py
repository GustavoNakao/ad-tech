import logging
from integracoes.baselinker.client import BaselinkerClient
from integracoes.gs1.client import get_gs1_client

logger = logging.getLogger(__name__)

class GeradorEANService:
    def __init__(self):
        self.bl_client  = BaselinkerClient()
        self.gs1_client = get_gs1_client()  # reutiliza a instância singleton — token cacheado

    def processar_ean_avulso(self, sku: str, gpc: str) -> dict:
        dados_bl = self.bl_client.buscar_produto_por_sku(sku)
        if not dados_bl:
            return {"sucesso": False, "erro": f"SKU '{sku}' não encontrado no BaseLinker."}

        # --- Validações com mensagens detalhadas ---
        ncm = dados_bl.get("ncm", "").strip()
        if not ncm:
            return {
                "sucesso": False,
                "erro": (
                    "Produto sem NCM. Verifique se o campo 'NCM' está preenchido "
                    "nas features ou campos customizados do produto no BaseLinker."
                )
            }

        if dados_bl.get("peso_kg", 0) <= 0:
            return {"sucesso": False, "erro": "Produto sem Peso Bruto. Preencha o campo Peso no BaseLinker."}

        if dados_bl.get("marca", "").strip() in ("", "Marca Não Informada"):
            return {"sucesso": False, "erro": "Produto sem Marca. Preencha o campo Marca no BaseLinker."}

        # Valida imagem antes de chamar a GS1 — campo obrigatório
        imagem_url = dados_bl.get("imagem_url", "").strip()
        logger.info("SKU %s | imagem_url bruta recebida do BaseLinker: %r", sku, imagem_url)
        if not imagem_url:
            return {
                "sucesso": False,
                "erro": (
                    "Produto sem imagem. A GS1 exige ao menos uma foto do produto. "
                    "Adicione uma imagem ao produto no BaseLinker antes de gerar o EAN."
                )
            }

        # Log de confirmação antes de ir à GS1
        logger.info("SKU %s resolvido | NCM=%s | Marca=%s | Peso=%.3f kg | Imagem=%s",
                    sku, ncm, dados_bl["marca"], dados_bl["peso_kg"], imagem_url)

        # Geração na GS1
        resultado_gs1 = self.gs1_client.gerar_ean_completo(dados_bl, gpc)

        if not resultado_gs1["sucesso"]:
            return {"sucesso": False, "erro": resultado_gs1["erro"]}

        ean_novo = resultado_gs1["dados"].get("gtin")

        # Salva no BaseLinker
        salvo = self.bl_client.salvar_ean_no_produto(24542, dados_bl["id_produto"], ean_novo)

        return {
            "sucesso":      True,
            "ean":          ean_novo,
            "nome":         dados_bl["nome"],
            "sku":          sku,
            "ncm":          ncm,
            "foto_enviada": True,   # já validado acima — nunca chega aqui sem imagem
            "salvo_bl":     salvo,
        }