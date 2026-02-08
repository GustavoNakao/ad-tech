from django.core.management.base import BaseCommand
from inventarios.services.armazem_servico import ArmazemServico


class Command(BaseCommand):
    help = "Sincroniza armazéns do BaseLinker"

    def handle(self, *args, **options):
        self.stdout.write("Sincronizando armazéns...")
        resultado = ArmazemServico.sincronizar_todos()
        self.stdout.write(
            self.style.SUCCESS(
                f"Concluído! Total: {resultado['total']} | "
                f"Criadas: {resultado['criadas']} | "
                f"Atualizadas: {resultado['atualizadas']}"
            )
        )