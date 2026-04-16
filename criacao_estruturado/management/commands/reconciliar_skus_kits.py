"""
Job de reconciliação (15–60 min ou trigger-based).
Puxa SKUs da categoria de kits do BaseLinker, atualiza baselinker_kit_skus
e move SKUs ausentes (em HistoricoKit mas não no BL) para recycled_skus.
"""
import logging
from django.core.management.base import BaseCommand

from criacao_estruturado.services.criador_kits_service import CriadorKitsService

logger = logging.getLogger("gerador_kits")


class Command(BaseCommand):
    help = (
        "Reconcilia SKUs da categoria de kits: puxa do BaseLinker, "
        "atualiza baselinker_kit_skus e move ausentes para recycled."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval",
            type=int,
            default=0,
            help="Se > 0, roda em loop a cada N segundos (ex: 900 = 15 min)",
        )

    def handle(self, *args, **options):
        interval = options.get("interval") or 0
        servico = CriadorKitsService()

        if interval > 0:
            self.stdout.write(
                self.style.WARNING(f"Reconciliação em loop a cada {interval}s (Ctrl+C para parar)")
            )
            import time
            while True:
                try:
                    self._run_once(servico)
                except KeyboardInterrupt:
                    self.stdout.write(self.style.SUCCESS("Parado pelo usuário."))
                    break
                except Exception as e:
                    logger.exception("Erro na reconciliação: %s", e)
                    self.stdout.write(self.style.ERROR(f"Erro: {e}"))
                time.sleep(interval)
        else:
            self._run_once(servico)

    def _run_once(self, servico):
        resultado = servico._sync_imediato_baselinker_kit_skus()
        if resultado.get("sucesso"):
            self.stdout.write(
                self.style.SUCCESS(
                    f"Reconciliação OK: +{resultado.get('atualizados', 0)} atualizados, "
                    f"+{resultado.get('reciclados', 0)} reciclados"
                )
            )
        else:
            self.stdout.write(
                self.style.ERROR(f"Reconciliação falhou: {resultado.get('erro', '?')}")
            )
