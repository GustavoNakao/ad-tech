from django.core.management.base import BaseCommand
from inventarios.services.inventario_servico import InventarioServico


class Command(BaseCommand):
    help = "Sincroniza inventários do BaseLinker"

    def handle(self, *args, **options):
        self.stdout.write("Sincronizando inventários...")
        try:
            resultado = InventarioServico.sincronizar_todos()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Concluído! Total: {resultado['total']} | "
                    f"Criadas: {resultado['criadas']} | "
                    f"Atualizadas: {resultado['atualizadas']}"
                )
            )
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Erro durante a sincronização: {str(e)}"))