from django.core.management.base import BaseCommand
from pedidos.services.sincronizador_pedidos_service import SincronizadorPedidosService


class Command(BaseCommand):
    help = 'Sincroniza pedidos do BaseLinker'

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING("🚀 Iniciando sincronização..."))
        service = SincronizadorPedidosService()
        resultado = service.executar_sincronizacao(dias_atras=7)
        if resultado['sucesso']:
            self.stdout.write(self.style.SUCCESS(f"✅ {resultado['msg']}"))
        else:
            self.stdout.write(self.style.ERROR(f"❌ {resultado['msg']}"))