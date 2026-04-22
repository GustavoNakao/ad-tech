# inventarios/services/inventario_servico.py

import logging
from typing import Dict
from django.utils import timezone

from integracoes.baselinker.client import BaselinkerClient
from ..models.inventario_baselinker import InventarioBaselinker

logger = logging.getLogger(__name__)

class InventarioServico:
    @staticmethod
    def sincronizar_todos() -> Dict[str, int]:
        client = BaselinkerClient()
        try:
            dados = client.get_inventories()

            if dados.get("status") != "SUCCESS":
                erro = dados.get("error_message", "Erro desconhecido")
                codigo = dados.get("error_code", "???")
                logger.error(f"BaseLinker getInventories falhou: [{codigo}] {erro}")
                return {"total": 0, "criadas": 0, "atualizadas": 0}

            inventarios = dados.get("inventories", [])
            criadas = atualizadas = 0

            for inv in inventarios:
                obj, criada = InventarioBaselinker.objects.update_or_create(
                    inventory_id=inv["inventory_id"],
                    defaults={
                        "nome": inv["name"],
                        "descricao": inv.get("description", ""),
                        "eh_padrao": inv.get("is_default", False),
                        "armazens_vinculados": inv.get("warehouses", []),
                        "idiomas": inv.get("languages", []),
                    }
                )
                if criada:
                    criadas += 1
                else:
                    atualizadas += 1
                    # Atualiza o campo de sincronização apenas se já existia
                    obj.sincronizado_em = timezone.now()
                    obj.save(update_fields=["sincronizado_em"])

            logger.info(f"Sincronização de inventários concluída: {len(inventarios)} processados")
            return {
                "total": len(inventarios),
                "criadas": criadas,
                "atualizadas": atualizadas
            }

        except Exception as e:
            logger.exception("Erro inesperado ao sincronizar inventários do BaseLinker")
            return {"total": 0, "criadas": 0, "atualizadas": 0}