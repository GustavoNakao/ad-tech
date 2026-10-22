import time
import logging
from typing import Dict, List, Tuple
from django.core.management.base import BaseCommand
from integracoes.baselinker.client import BaselinkerClient, BaselinkerAPIError
from criacao_estruturado.models import HistoricoKit

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Atualiza os 96 SKUs de kits criados (20000..20095 -> 31000..31095) diretamente no BaseLinker."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Apenas lista as alterações sem enviar para o BaseLinker.",
        )

    def handle(self, *args, **options):
        dry_run: bool = options["dry_run"]
        self.stdout.write(self.style.NOTICE("Iniciando atualização de SKUs no BaseLinker..."))

        kits = HistoricoKit.objects.filter(sku_kit__gte="31000", sku_kit__lte="31095").order_by("sku_kit")
        total = kits.count()

        if total == 0:
            self.stdout.write(self.style.WARNING("Nenhum kit encontrado na faixa 31000..31095."))
            return

        self.stdout.write(f"Encontrados {total} kits para atualizar no BaseLinker.")

        if dry_run:
            self.stdout.write(self.style.WARNING("[DRY-RUN] Nenhuma chamada à API será feita."))
            for k in kits:
                sku_antigo = str(int(k.sku_kit) - 11000)
                self.stdout.write(f"  ID BaseLinker: {k.id_kit} | SKU Atual: {sku_antigo} -> Novo SKU: {k.sku_kit}")
            return

        client = BaselinkerClient()
        sucessos = 0
        falhas = 0

        for k in kits:
            sku_novo = k.sku_kit
            sku_antigo = str(int(k.sku_kit) - 11000)
            id_kit = int(k.id_kit)

            payload = {
                "inventory_id": 24542,
                "product_id": id_kit,
                "sku": sku_novo,
            }

            try:
                resp = client._request("addInventoryProduct", payload)
                if resp.get("status") == "SUCCESS":
                    sucessos += 1
                    self.stdout.write(self.style.SUCCESS(
                        f"OK: ID {id_kit} alterado de {sku_antigo} para {sku_novo}"
                    ))
                else:
                    falhas += 1
                    self.stdout.write(self.style.ERROR(
                        f"FALHA ID {id_kit}: {resp.get('error_message')}"
                    ))
            except Exception as exc:
                falhas += 1
                self.stdout.write(self.style.ERROR(
                    f"ERRO ID {id_kit} ({sku_antigo} -> {sku_novo}): {exc}"
                ))

            time.sleep(0.3)

        self.stdout.write(self.style.SUCCESS(
            f"Processamento concluído: {sucessos} alterados com sucesso, {falhas} falhas."
        ))
