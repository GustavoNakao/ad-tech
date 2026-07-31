import re
import logging
from django.core.management.base import BaseCommand
from criacao_estruturado.models.historico_kit import HistoricoKit
from criacao_estruturado.services.criador_kits_service import CriadorKitsService, ajustar_titulo, formatar_title_case

logger = logging.getLogger("django")

class Command(BaseCommand):
    help = "Reformat existing titles in the database to strictly follow: [Quantity] [Product Type] [Format/Specs] [Brand]."

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Apply the new titles directly to BaseLinker.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Limit the number of products processed (useful for testing).",
        )
        parser.add_argument(
            "--sku",
            type=str,
            help="Filter execution by a specific kit SKU.",
        )
        parser.add_argument(
            "--use-gemini",
            action="store_true",
            help="Use Gemini 2.5 Flash instead of the fast programmatic cleaner.",
        )

    def handle(self, *args, **options):
        commit = options["commit"]
        limit = options["limit"]
        sku_filtro = options["sku"]
        use_gemini = options["use_gemini"]

        service = CriadorKitsService()
        client = service.client
        inventory_id = service.ID_INVENTARIO

        query = HistoricoKit.objects.all()
        if sku_filtro:
            query = query.filter(sku_kit=sku_filtro)

        historicos = list(query)
        if limit > 0:
            historicos = historicos[:limit]

        total = len(historicos)
        self.stdout.write(self.style.WARNING(f"Analyzing {total} records (Gemini: {use_gemini})..."))

        # Palavras de padding/SEO conhecidas para remover do final dos títulos
        PADDING_WORDS = {
            # Suporte / Mão Francesa
            "parede", "fixador", "fixação", "fixacao", "organizador", "suporte", "prateleira", "aço", "aco", "decor", "decoração", "decoracao",
            # Sifão / Válvula / Torneira
            "pia", "lavatório", "lavatorio", "cozinha", "sanfonado", "flexível", "flexivel", "ralo", "banheiro", "metal", "tanque",
            "bancada", "bica", "móvel", "movel", "articulada", "externa", "jardim", "area", "área", "válvula", "valvula",
            # Feltro / Adesivo
            "protetor", "móveis", "moveis", "cadeiras", "cadeira", "piso", "adesivo", "quadrado", "redondo",
            # Anilina / Corante / Tinta
            "tingidor", "tingimento", "madeira", "álcool", "alcool", "corante", "anilina", "tinta", "spray", "aerossol", "acabamento",
            # Shampoo / Sabonete / Higiene
            "fios", "cabelo", "cabelos", "anticaspa", "detox", "limpeza", "geral", "baby", "infantil", "higiene", "neutro", "glicerina",
            # Lápis
            "cor", "escolar", "desenho", "sextavado", "faber", "castell",
            # Outros fillers comuns de fim de título
            "forte", "fortes", "eficaz", "eficiente", "poderoso", "poderosa", "potente", "potentes", "turbo",
            "fácil", "facil", "prático", "pratico", "resistente", "durável", "duravel", "completo", "moderno",
            "versátil", "versatil", "multifuncional", "compacto", "novo", "original", "premium", "classico", "clássico",
            "reparo", "conserto", "reparos"
        }

        def limpar_titulo_programatico(titulo_atual):
            titulo = titulo_atual
            titulo = " ".join(titulo.split()).strip()
            while True:
                palavras = titulo.split()
                if not palavras:
                    break
                ultima = palavras[-1].lower().strip(" -.,/")
                if ultima in PADDING_WORDS:
                    palavras.pop()
                    titulo = " ".join(palavras)
                else:
                    break
            palavras_fim_proibidas = {"e", "para", "com", "de", "o", "a", "os", "as", "em", "do", "da", "dos", "das"}
            while True:
                palavras = titulo.split()
                if not palavras:
                    break
                ultima = palavras[-1].lower().strip(" -.,/")
                if ultima in palavras_fim_proibidas:
                    palavras.pop()
                    titulo = " ".join(palavras)
                else:
                    break
            return formatar_title_case(titulo.strip())

        PROMPT_REFORMAT = """Você é um assistente especialista em reescrever e limpar títulos de produtos de e-commerce.
Sua única tarefa é receber um título atual e reescrevê-lo exatamente no seguinte formato neutro e descritivo:
[Quantidade] [Tipo de Produto] [Formato / Especificação real do produto / Modelo / Compatibilidade] [Marca]

====================================
DIRETRIZES RÍGIDAS DE FORMATAÇÃO:
====================================
1. Quantidade: Coloque apenas se for kit (ex: Kit 2, Kit 3, Kit 4). Para unitário, deixe vazio.
2. Tipo de Produto: Categoria do produto (ex: Cola Vinil, Limpa Pedras, Suporte TV, Tira Manchas, Fio Dental, Shampoo).
3. Formato / Especificação / Modelo / Compatibilidade: Características físicas ou modelo técnico, ou compatibilidade (ex: "17g", "Fixo 14 a 101 Polegadas", "para Reboco", "para Madeira", "200ml"). Use "para" ou "compatível com" quando fizer sentido.
4. Marca: Marca oficial e modelo (ex: Tekbond, Sanol Pro, Promabond, Clear, Dove, Multivisao).
5. NUNCA inclua adjetivos subjetivos de qualidade (Premium, Clássico, Suprema, Excelente) ou promessas de resultado/força (Forte, Eficaz, Poderoso, Potente, Remove Manchas, Tira Encardido, Ação Rápida).
6. NENHUM caractere especial ou pontuação (- , . ! ? % & @ # / [ ] ( )). Separe apenas por espaço.
7. Retorne EXCLUSIVAMENTE o título novo formatado em Title Case, sem aspas, explicações, markdown ou introdução."""

        import time
        from concurrent.futures import ThreadPoolExecutor
        from integracoes.gemini import GeminiClient
        gemini = GeminiClient() if use_gemini else None

        def reformatar_um(pid_int, titulo_atual, sku):
            max_retries = 6
            for attempt in range(max_retries):
                try:
                    user_input = f"Título atual: '{titulo_atual}'"
                    res = gemini.chat(
                        system_prompt=PROMPT_REFORMAT,
                        user_input=user_input,
                        modelo="gemini-2.5-flash"
                    )
                    if res is None:
                        time.sleep(12)
                        continue
                    titulo_limpo = res.strip().replace('"', '').replace("'", "")
                    titulo_ajustado = ajustar_titulo(titulo_limpo, min_c=0, max_c=60)
                    titulo_final = formatar_title_case(titulo_ajustado)
                    return pid_int, sku, titulo_atual, titulo_final, None
                except Exception as ex:
                    if "429" in str(ex):
                        time.sleep(15)
                        continue
                    return pid_int, sku, titulo_atual, None, ex
            return pid_int, sku, titulo_atual, None, Exception("Gemini rate limits or cooldown exhausted.")

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
                r = client._request(
                    "getInventoryProductsData",
                    {"inventory_id": inventory_id, "products": pids},
                )
                produtos = r.get("products", {})
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error fetching batch from BaseLinker: {e}"))
                erros += len(lote)
                continue

            # Prepara tarefas
            tarefas = []
            for pid_int in pids:
                hist = mapa_hist.get(pid_int)
                if not hist:
                    continue
                sku = hist.sku_kit
                pid = str(pid_int)
                raw = produtos.get(pid) or produtos.get(pid_int)
                if not raw:
                    continue
                titulo_atual = (raw.get("text_fields", {}) or {}).get("name") or raw.get("name") or ""
                if not titulo_atual:
                    continue
                tarefas.append((pid_int, titulo_atual, sku))

            res_lote = []
            if use_gemini:
                # Executa com ThreadPool
                with ThreadPoolExecutor(max_workers=4) as executor:
                    futures = [executor.submit(reformatar_um, *t) for t in tarefas]
                    for fut in futures:
                        try:
                            pid_int, sku, titulo_atual, titulo_final, err = fut.result()
                            if err:
                                self.stdout.write(self.style.ERROR(f"  -> Gemini error for {sku}: {err}"))
                                erros += 1
                            elif titulo_final and titulo_final != titulo_atual:
                                res_lote.append((pid_int, sku, titulo_atual, titulo_final))
                        except Exception as e_fut:
                            self.stdout.write(self.style.ERROR(f"  -> Thread execution error: {e_fut}"))
                            erros += 1
            else:
                # Limpeza programática super rápida e sem limite de cota
                for pid_int, titulo_atual, sku in tarefas:
                    try:
                        titulo_final = limpar_titulo_programatico(titulo_atual)
                        if titulo_final != titulo_atual:
                            res_lote.append((pid_int, sku, titulo_atual, titulo_final))
                    except Exception as e_prog:
                        self.stdout.write(self.style.ERROR(f"  -> Cleanup error for {sku}: {e_prog}"))
                        erros += 1

            # Executa updates sequenciais no BaseLinker
            for pid_int, sku, titulo_atual, titulo_final in res_lote:
                self.stdout.write(self.style.WARNING(f"[{sku}] PID: {pid_int}"))
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
                        self.stdout.write(self.style.SUCCESS(f"  -> [COMMIT] Title updated successfully."))
                    except Exception as e_up:
                        erros += 1
                        self.stdout.write(self.style.ERROR(f"  -> [COMMIT] Error updating kit {sku}: {e_up}"))
                else:
                    self.stdout.write(self.style.NOTICE(f"  -> [DRY-RUN] No change was saved."))

        self.stdout.write(self.style.SUCCESS(
            f"\nReformat completed. Processed: {total} | Changed: {alterados} | Errors: {erros}."
        ))
        if not commit:
            self.stdout.write(self.style.WARNING("ATTENTION: Dry-run mode. Use '--commit' to save changes."))
