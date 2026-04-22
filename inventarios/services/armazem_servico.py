from typing import Dict
from integracoes.baselinker.client import BaselinkerClient
from ..models.armazem_baselinker import ArmazemBaselinker


class ArmazemServico:
    @staticmethod
    def sincronizar_todos() -> Dict:
        client = BaselinkerClient()
        dados = client.get_inventory_warehouses()
        armazens = dados.get("warehouses", [])

        criadas = atualizadas = 0
        for a in armazens:
            obj, criada = ArmazemBaselinker.objects.update_or_create(
                warehouse_id=a["warehouse_id"],
                defaults={
                    "nome": a["name"],
                    "tipo": a["warehouse_type"],
                    "eh_padrao": a.get("is_default", False),
                }
            )
            if criada:
                criadas += 1
            else:
                atualizadas += 1

        return {"total": len(armazens), "criadas": criadas, "atualizadas": atualizadas}