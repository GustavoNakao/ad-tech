import time
import logging
from typing import Dict, List, Tuple
from django.core.management.base import BaseCommand
from integracoes.baselinker.client import BaselinkerClient, BaselinkerAPIError
from criacao_estruturado.models import HistoricoKit, GtinKit

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Atualiza os 96 SKUs e EANs de kits criados (faixa 50000..50095) diretamente no BaseLinker."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Apenas lista as alterações sem enviar para o BaseLinker.",
        )

    def handle(self, *args, **options):
        dry_run: bool = options["dry_run"]
        self.stdout.write(self.style.NOTICE("Iniciando atualização de SKUs e EANs no BaseLinker..."))

        kits = HistoricoKit.objects.filter(sku_kit__gte="50000", sku_kit__lte="50095").order_by("sku_kit")
        total = kits.count()

        if total == 0:
            self.stdout.write(self.style.WARNING("Nenhum kit encontrado na faixa 50000..50095."))
            return

        self.stdout.write(f"Encontrados {total} kits para atualizar no BaseLinker.")

        if dry_run:
            self.stdout.write(self.style.WARNING("[DRY-RUN] Nenhuma chamada à API será feita."))
            for k in kits:
                gk = GtinKit.objects.filter(sku_kit=k.sku_kit).first()
                ean = gk.gtin if gk else ""
                sku_legado = str(int(k.sku_kit) - 30000)
                self.stdout.write(f"  ID BaseLinker: {k.id_kit} | SKU Legado: {sku_legado} -> Novo SKU: {k.sku_kit} | EAN: {ean}")
            return

        client = BaselinkerClient()
        sucessos = 0
        falhas = 0

        for k in kits:
            sku_novo = k.sku_kit
            sku_legado = str(int(k.sku_kit) - 30000)
            id_kit = int(k.id_kit)
            gk = GtinKit.objects.filter(sku_kit=k.sku_kit).first()
            ean = gk.gtin if gk else ""

            payload = {
                "inventory_id": 24542,
                "product_id": id_kit,
                "sku": sku_novo,
            }
            if ean:
                payload["ean"] = ean

            try:
                resp = client._request("addInventoryProduct", payload)
                if resp.get("status") == "SUCCESS":
                    sucessos += 1
                    self.stdout.write(self.style.SUCCESS(
                        f"OK: ID {id_kit} alterado de {sku_legado} para {sku_novo} (EAN: {ean})"
                    ))
                else:
                    falhas += 1
                    self.stdout.write(self.style.ERROR(
                        f"FALHA ID {id_kit}: {resp.get('error_message')}"
                    ))
            except Exception as exc:
                falhas += 1
                self.stdout.write(self.style.ERROR(
                    f"ERRO ID {id_kit} ({sku_legado} -> {sku_novo}): {exc}"
                ))

            time.sleep(0.3)

        self.stdout.write(self.style.SUCCESS(
            f"Processamento concluído: {sucessos} alterados com sucesso, {falhas} falhas."
        ))
