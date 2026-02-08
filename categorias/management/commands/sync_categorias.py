# categorias/management/commands/sync_categorias.py

from django.core.management.base import BaseCommand
from django.utils import timezone
from categorias.services.categoria_servico import CategoriaServico


class Command(BaseCommand):
    help = "Sincroniza todas as categorias do BaseLinker (inventário 24542 por padrão)"

    def add_arguments(self, parser):
        parser.add_argument(
            '--inventory-id',
            type=int,
            default=24542,
            help='ID do inventário no BaseLinker (padrão: 24542)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Força re-sincronização mesmo se já rodou hoje',
        )

    def handle(self, *args, **options):
        inventory_id = options['inventory_id']
        force = options['force']

        self.stdout.write(
            self.style.HTTP_INFO(
                f"Iniciando sincronização de categorias → Inventário ID: {inventory_id}"
            )
        )

        # Chama o serviço
        resultado = CategoriaServico.sincronizar_todas(inventory_id=inventory_id)

        # Exibe o resultado final
        if resultado["sucesso"]:
            estilo = self.style.SUCCESS if resultado['erros'] == 0 else self.style.WARNING
            self.stdout.write(
                estilo(
                    f"\nSincronização concluída!\n"
                    f"   Inventário   : {inventory_id}\n"
                    f"   Total na API : {resultado['total_api']}\n"
                    f"   Criadas      : {resultado['criadas']}\n"
                    f"   Atualizadas  : {resultado['atualizadas']}\n"
                    f"   Erros        : {resultado['erros']}"
                )
            )

            if resultado['erros'] > 0:
                self.stdout.write(
                    self.style.ERROR("Atenção: Houve erros durante o vínculo de categorias pai/filha. Verifique o log.")
                )
        else:
            self.stdout.write(self.style.ERROR("Falha total na sincronização. Veja o log para detalhes."))