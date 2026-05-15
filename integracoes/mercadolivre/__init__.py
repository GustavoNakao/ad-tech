from .client import MercadoLivreClient, ML_CATEGORIA_PADRAO

# Alias para manter compatibilidade com imports existentes
CATEGORIA_PADRAO_BASELINKER = ML_CATEGORIA_PADRAO

__all__ = ["MercadoLivreClient", "ML_CATEGORIA_PADRAO", "CATEGORIA_PADRAO_BASELINKER"]
