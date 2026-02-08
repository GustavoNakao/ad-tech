from django.core.management.base import BaseCommand
from criacao_estruturado.services.criador_kits_service import CriadorKitsService


class Command(BaseCommand):
    help = "Cria kits para produtos estruturados novos (SKU > 30567)"

    def handle(self, *args, **options):
        self.stdout.write(self.style.WARNING("🚀 Iniciando automação de Kits..."))

        servico = CriadorKitsService()

        # 1. Calibragem Inicial
        self.stdout.write("🔄 Verificando integridade da sequência de SKUs...")
        calib = servico.calibrar_contador_com_baselinker()
        if calib["sucesso"]:
            self.stdout.write(self.style.SUCCESS(f"Status: {calib['msg']}"))
        else:
            self.stdout.write(self.style.ERROR(f"Erro na calibração: {calib['msg']}"))
            return  # Aborta se não conseguir ler o BL

        criados_sessao = 0
        erros_sessao = 0

        # 2. Busca e Processamento (Loop Otimizado)
        # O método gerar_candidatos() agora cuida da paginação inteligentemente
        for candidato in servico.gerar_candidatos():

            pid = candidato["pid"]
            sku_origem = candidato.get("sku_origem") or candidato.get("sku")
            nome = candidato.get("nome", sku_origem or "<sem nome>")

            self.stdout.write(f"\n🔍 Processando: {nome} (SKU Origem: {sku_origem})...")

            res = servico.executar_criacao(pid, sku_origem)

            if res["sucesso"]:
                self.stdout.write(self.style.SUCCESS(f"✅ SUCESSO! Kit criado: {res['sku_novo']}"))
                criados_sessao += 1
            else:
                self.stdout.write(self.style.ERROR(f"❌ FALHA: {res['msg']}"))
                erros_sessao += 1

        # Resumo final
        self.stdout.write(self.style.SUCCESS(f"\n🏁 Processo Finalizado."))
        self.stdout.write(f"Novos Kits: {criados_sessao} | Falhas: {erros_sessao}")