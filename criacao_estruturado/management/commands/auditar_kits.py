from django.core.management.base import BaseCommand
from criacao_estruturado.services.criador_kits_service import CriadorKitsService


class Command(BaseCommand):
    help = 'Audita a sequência de SKUs para encontrar buracos (números deletados) e recuperá-los.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING("🕵️  Iniciando auditoria de SKUs..."))

        # Instancia o serviço
        service = CriadorKitsService()

        # Chama a função de auditoria (verifique se você adicionou ela no service!)
        if hasattr(service, 'auditar_sequencia_completa'):
            resultado = service.auditar_sequencia_completa()

            if resultado['sucesso']:
                self.stdout.write(self.style.SUCCESS(f"✅ {resultado['msg']}"))
            else:
                self.stdout.write(self.style.ERROR(f"❌ Erro na auditoria: {resultado['msg']}"))
        else:
            self.stdout.write(self.style.ERROR(
                "❌ Erro: O método 'auditar_sequencia_completa' não foi encontrado no CriadorKitsService."))