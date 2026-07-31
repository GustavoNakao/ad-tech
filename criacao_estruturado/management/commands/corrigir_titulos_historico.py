import re
import logging
from django.core.management.base import BaseCommand
from criacao_estruturado.models.historico_kit import HistoricoKit
from criacao_estruturado.services.criador_kits_service import CriadorKitsService, ajustar_titulo, formatar_title_case

logger = logging.getLogger("django")

class Command(BaseCommand):
    help = "Varre o histórico de kits criados em lote e corrige títulos que contêm termos proibidos ou incompatíveis com as regras do Mercado Livre."

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Efetiva as alterações de título diretamente no BaseLinker.",
        )
        parser.add_argument(
            "--sku",
            type=str,
            help="Filtra a execução para um SKU de kit específico.",
        )

    def handle(self, *args, **options):
        commit = options["commit"]
        sku_filtro = options["sku"]

        service = CriadorKitsService()
        client = service.client
        inventory_id = service.ID_INVENTARIO

        # Procura os registros do histórico
        query = HistoricoKit.objects.all()
        if sku_filtro:
            query = query.filter(sku_kit=sku_filtro)

        historicos = list(query)
        total = len(historicos)
        self.stdout.write(self.style.WARNING(f"Encontrados {total} registros de histórico para analisar..."))

        termos_proibidos = [
            # ── Adjetivos subjetivos de qualidade ─────────────────────────────
            "Premium", "Clássico", "Classico", "Ouro", "Bronze", "Prata", "Special",
            "Suprema", "Supremo", "Excelente", "Superior", "Incrível", "Incrivel",
            "Surpreendente", "Extraordinário", "Extraordinario", "Espetacular",
            "Magnífico", "Magnifico", "Inigualável", "Inigualavel", "Excepcional",
            "Profissional", "Melhor", "Melhor do Mercado", "Alta Qualidade",
            "Ótima Qualidade", "Otima Qualidade", "Boa Qualidade", "Qualidade Superior",
            "Top", "Top de Linha", "Linha Premium", "Exclusivo", "Luxo", "Luxuoso",
            "Sofisticado", "Elegante", "Requintado", "Diferenciado", "Inovador",

            # ── Termos comerciais e de promoção ───────────────────────────────
            "Original", "Oferta", "Promoção", "Promocao", "Desconto", "Barato",
            "Econômico", "Economico", "Custo Benefício", "Custo Beneficio",
            "Valor Especial", "Preço Justo", "Preco Justo", "Oportunidade",
            "Super Oferta", "Mega Oferta", "Imperdível", "Imperdivel",
            "Lançamento", "Lancamento", "Novidade", "Estoque Limitado",

            # ── Promessas de confiança / garantia ────────────────────────────
            "Confiança", "Confianca", "Confiável", "Confiavel",
            "Garantia", "Garantido", "Garantimos", "Com Garantia",
            "Satisfação Garantida", "Satisfacao Garantida",
            "Aprovado", "Certificado", "Testado", "Aprovado por",
            "Original Garantido", "100% Original",

            # ── Promessas de logística / entrega ─────────────────────────────
            "Envio Rápido", "Envio Rapido", "Envio Imediato", "Envio Hoje",
            "Pronta Entrega", "Entrega Rápida", "Entrega Rapida", "Entrega Imediata",
            "Despacho Imediato", "Despacho Rápido", "Despacho Rapido",
            "Frete Grátis", "Frete Gratis",

            # ── Termos vazios / genéricos / filler ────────────────────────────
            "resistente", "resistência", "resistencia",
            "prático", "pratico", "praticidade",
            "completo", "completamente",
            "para casa", "uso diário", "uso diario",
            "moderno", "moderninho", "funcional",
            "durável", "duravel",
            "compacto",
            "Cuidado Diário", "Cuidado Diario", "Cuidado Com",
            "Dia a Dia", "Do Dia a Dia",
            "Versatil", "Versátil",
            "Multifuncional",

            # ── Promessas de resultado / performance ───────────────────────────
            "Forte", "Fortes",
            "Eficaz", "Eficiente",
            "Poderoso", "Poderosa",
            "Potente", "Potentes",
            "Turbo",
            "Ação Rápida", "Acao Rapida", "Ação Rapida", "Acao Rápida",
            "Pintura Rápida", "Pintura Rapida",
            "Reparo Rápido", "Reparo Rapido",
            "Alto Rendimento",

            # ── Verbos de resultado no título ────────────────────────────────
            "Remove Manchas", "Remove Impurezas", "Remove Resíduos",
            "Remove Gordura", "Remove Calcário",
            "Limpa Tudo",
            "Substitui",

            # ── Implica superacão de dificuldade / resultado sobre sujeira ─────
            "Difíceis", "Dificeis",
            "Encardido", "Encardidos",

            # ── Comparação e imitação de marcas ───────────────────────────────
            "similar", "similar a", "igual a", "tipo", "estilo",
            "cópia", "copia", "réplica", "replica", "imitação", "imitacao",
            "inspired by", "inspired", "parecido com",

            # ── Termos de saúde / ação farmacológica (ANVISA) ─────────────────
            "antisséptico", "antisseptico", "anti-séptico", "anti-septico",
            "anti-inflamatório", "anti-inflamatorio", "antiinflamatório", "antiinflamatorio",
            "antibiótico", "antibiotico",
            "expectorante", "broncodilatador", "analgésico", "analgesico",
            "sedativo", "sedante", "cicatrizante", "cicatrização",
            "diurético", "diuretico", "laxante", "laxativo",
            "antifúngico", "antifungico", "antiviral", "antibacteriano",
            "antioxidante", "antimicrobiano", "antialérgico",
            "antialérgico", "antialergico", "antiparasitário", "antiparasitario",
            "anti-histamínico", "anti-histaminico",

            # ── Cura, tratamento e prevenção de doenças ──────────────────────
            "cura", "curar", "trata", "tratar", "tratamento",
            "combate", "combater", "previne", "prevenir",
            "elimina", "eliminar", "acabar com",
            "alívio", "alivio", "alívio imediato", "alivio imediato",
            "remédio", "remedio", "medicação", "medicacao",
            "proteção natural", "protecao natural",
            "age na", "atua no", "ação direta", "acao direta",

            # ── Sintomas e doenças ────────────────────────────────────────────
            "tosse", "gripe", "resfriado", "dor de garganta",
            "colesterol", "diabetes", "insônia", "insonia",
            "ansiedade", "depressão", "depressao",
            "dor nas juntas", "artrite", "catarro", "muco",
            "congestão", "congestao", "arranhado", "rouquidão", "rouquidao",
            "irritação", "irritacao", "inflamação", "inflamacao",
            "febre", "náusea", "nausea", "diarreia",

            # ── Termos de prescrição / posologia ─────────────────────────────
            "posologia", "bula", "dosagem", "dose recomendada",
            "pacientes", "sob prescrição", "sob prescricao",
            "uso terapêutico", "uso terapeutico",
            "tratamento de", "tratamento para",
        ]

        padrões_regex = []
        for termo in termos_proibidos:
            if " " in termo:
                padrões_regex.append((re.compile(re.escape(termo), re.IGNORECASE), termo))
            else:
                padrões_regex.append((re.compile(r'\b' + re.escape(termo) + r'\b', re.IGNORECASE), termo))

        alterados = 0
        erros = 0
        lote_tamanho = 100

        for i in range(0, total, lote_tamanho):
            lote = historicos[i:i + lote_tamanho]
            pids = []
            mapa_hist = {}
            for h in lote:
                try:
                    pids.append(int(h.id_kit))
                    mapa_hist[int(h.id_kit)] = h
                except ValueError:
                    pass

            if not pids:
                continue

            try:
                # Busca todos os produtos do lote no BaseLinker em uma única chamada
                r = client._request(
                    "getInventoryProductsData",
                    {"inventory_id": inventory_id, "products": pids},
                )
                produtos = r.get("products", {})
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Erro ao buscar lote de produtos no BaseLinker: {e}"))
                erros += len(lote)
                continue

            for pid_int in pids:
                hist = mapa_hist.get(pid_int)
                if not hist:
                    continue

                sku = hist.sku_kit
                pid = str(pid_int)

                raw = produtos.get(pid) or produtos.get(pid_int)
                if not raw:
                    self.stdout.write(self.style.ERROR(f"  -> Produto {pid} (SKU {sku}) não encontrado no BaseLinker."))
                    continue

                titulo_atual = (raw.get("text_fields", {}) or {}).get("name") or raw.get("name") or ""
                if not titulo_atual:
                    continue

                # Verifica se o título possui algum termo proibido
                contem_proibido = False
                titulo_limpo = titulo_atual

                for regex, termo in padrões_regex:
                    if regex.search(titulo_limpo):
                        contem_proibido = True
                        titulo_limpo = regex.sub("", titulo_limpo)

                if not contem_proibido:
                    continue

                # Limpa espaços e ajusta usando as regras do sistema
                titulo_limpo = " ".join(titulo_limpo.split()).strip()
                titulo_ajustado = ajustar_titulo(titulo_limpo, min_c=55, max_c=60)
                titulo_final = formatar_title_case(titulo_ajustado)

                if titulo_final == titulo_atual:
                    continue

                self.stdout.write(self.style.WARNING(f"[{sku}] PID: {pid}"))
                self.stdout.write(self.style.WARNING(f"  -> DE:   '{titulo_atual}'"))
                self.stdout.write(self.style.WARNING(f"  -> PARA: '{titulo_final}'"))

                alterados += 1

                if commit:
                    try:
                        update_payload = {
                            "inventory_id": inventory_id,
                            "product_id": pid_int,
                            "text_fields": {
                                "name": titulo_final,
                            }
                        }
                        client._request("addInventoryProduct", update_payload)
                        self.stdout.write(self.style.SUCCESS(f"  -> [COMMIT] Título atualizado com sucesso no BaseLinker."))
                    except Exception as e_up:
                        erros += 1
                        self.stdout.write(self.style.ERROR(f"  -> [COMMIT] Erro ao atualizar kit {sku}: {e_up}"))
                else:
                    self.stdout.write(self.style.NOTICE(f"  -> [DRY-RUN] Nenhuma alteração foi gravada (use --commit para aplicar)."))

        self.stdout.write(self.style.SUCCESS(
            f"\nProcessamento concluído. Analisados: {total} | Alterados: {alterados} | Erros: {erros}."
        ))
        if not commit:
            self.stdout.write(self.style.WARNING("ATENÇÃO: Executado em modo DRY-RUN. Use o parâmetro '--commit' para gravar as alterações."))
