"""
Comando de sincronização de pedidos BaseLinker.

Uso:
  # Puxar histórico COMPLETO da conta (use na primeira vez)
  python manage.py sync_pedidos --historico-completo

  # Atualização incremental (últimos 7 dias, padrão)
  python manage.py sync_pedidos

  # Últimos N dias
  python manage.py sync_pedidos --dias 30
"""

import logging
import time

from django.core.management.base import BaseCommand

from pedidos.services.sincronizador_pedidos_service import SincronizadorPedidosService

logger = logging.getLogger('sincronizador_pedidos')


class Command(BaseCommand):
    help = 'Sincroniza pedidos do BaseLinker com o banco de dados local.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--historico-completo',
            action='store_true',
            default=False,
            help='Importa TODOS os pedidos da conta (ignora --dias). Use na primeira sincronização.',
        )
        parser.add_argument(
            '--dias',
            type=int,
            default=7,
            help='Número de dias a sincronizar (padrão: 7). Ignorado se --historico-completo for passado.',
        )

    def handle(self, *args, **options):
        historico_completo = options['historico_completo']
        dias = options['dias']

        # ── Cabeçalho ─────────────────────────────────────────────────────────
        self.stdout.write('')
        self.stdout.write(self.style.HTTP_INFO('═' * 60))
        self.stdout.write(self.style.HTTP_INFO('  🔄  SINCRONIZAÇÃO BASELINKER → PEDIDOS'))
        self.stdout.write(self.style.HTTP_INFO('═' * 60))

        if historico_completo:
            self.stdout.write(self.style.WARNING('  Modo: HISTÓRICO COMPLETO'))
        else:
            self.stdout.write(self.style.WARNING(f'  Modo: últimos {dias} dias'))

        self.stdout.write('')

        # ── Execução ──────────────────────────────────────────────────────────
        inicio = time.time()
        service = SincronizadorPedidosService()

        try:
            resultado = service.executar_sincronizacao(
                historico_completo=historico_completo,
                dias_atras=dias,
            )
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'\n  ❌ Erro inesperado: {e}'))
            logger.exception("Erro inesperado durante sync_pedidos")
            return

        duracao = time.time() - inicio
        totais = resultado.get('totais', {})

        # ── Resultado ─────────────────────────────────────────────────────────
        self.stdout.write(self.style.HTTP_INFO('═' * 60))

        if resultado['sucesso']:
            self.stdout.write(self.style.SUCCESS('  ✅ Sincronização concluída com sucesso!\n'))
            self.stdout.write(f"  📦 Total processados : {totais.get('processados', 0)}")
            self.stdout.write(self.style.SUCCESS(f"  ✨ Criados           : {totais.get('criados', 0)}"))
            self.stdout.write(self.style.WARNING(f"  🔄 Atualizados       : {totais.get('atualizados', 0)}"))
            self.stdout.write(f"  ⏭️  Ignorados         : {totais.get('ignorados', 0)}")
            if totais.get('erros', 0) > 0:
                self.stdout.write(self.style.ERROR(f"  💥 Erros             : {totais.get('erros', 0)}"))
            else:
                self.stdout.write(f"  💥 Erros             : 0")
        else:
            self.stdout.write(self.style.ERROR(f"  ❌ Falha: {resultado['msg']}"))

        self.stdout.write(f"\n  ⏱️  Tempo total: {duracao:.1f}s")
        self.stdout.write(self.style.HTTP_INFO('═' * 60))
        self.stdout.write('')
