import re
import logging
import hashlib
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from criacao_estruturado.models.historico_kit import HistoricoKit
from criacao_estruturado.models.gtin_kit import GtinKit
from criacao_estruturado.services.criador_kits_service import (
    CriadorKitsService,
    ajustar_descricao_quantidade,
)
from integracoes.gemini.client import GeminiClient

logger = logging.getLogger("django")

PROMPT_REWRITE_DESC = """Você é um redator de e-commerce e especialista em compliance da ANVISA e do Mercado Livre.
Sua tarefa é reescrever a descrição de produto fornecida para torná-la 100% em conformidade com as regras da ANVISA e políticas do Mercado Livre.

REGRAS ABSOLUTAS:
1. REMOVA QUALQUER AÇÃO FARMACOLÓGICA (NUNCA UTILIZE):
   - "antisséptico", "antissépitco", "antisséptica", "antissépticas", "antissépticos"
   - "anti-inflamatório", "anti-inflamatória", "anti-inflamatórias", "anti-inflamatórios"
   - "expectorante", "expectorantes"
   - "broncodilatador", "broncodilatadora", "broncodilatadoras", "broncodilatadores"
   - "propriedades terapêuticas"
   Substitua por termos factuais neutros: "ação refrescante", "fórmula umectante", "cuidado com ingredientes naturais".

2. REMOVA QUALQUER DOENÇA, SINTOMA OU ANATOMIA CLÍNICA (NUNCA UTILIZE):
   - "desconforto da garganta", "desconforto de garganta", "irritação da garganta", "dor de garganta"
   - "tosse", "gripe", "resfriado", "rouquidão", "rouquidao", "irritação", "arranhado", "muco", "congestão", "congestao", "catarro".
   Substitua por termos focados na experiência e bem-estar: "manutenção do hálito fresco", "aromatizante bucal", "bem-estar no dia a dia", "frescor".

3. REMOVA QUALQUER PROMESSA DE CURA OU ALÍVIO RÁPIDO (NUNCA UTILIZE):
   - "alívio imediato", "alivio imediato", "alivia rapidamente"
   - "auxiliando na recuperação", "recuperação da garganta"
   - "prevenindo novas irritações", "combate a causa", "eliminação do muco", "eliminar o muco"
   Substitua por termos realistas: "promove bem-estar", "auxilia no conforto", "ideal para a rotina".

4. REMOVA QUALQUER DOSAGEM/POSOLOGIA MÉDICA:
   - Na FAQ ou no texto, nunca diga "aplicar de X a Y borrifadas" ou "de X em X horas".
   Substitua por uma sugestão de consumo livre: "Sugestão de consumo: aplique sempre que desejar para sensação de frescor."

5. ADICIONE O DISCLAIMER ANVISA NO FINAL:
   No final do texto (na última linha), adicione exatamente o parágrafo:
   "Este produto é um suplemento alimentar/aromatizante e não um medicamento. Em caso de dúvidas, consulte um médico."

6. ESTILO DO TEXTO:
   - NÃO use formatação Markdown (asteriscos, hashtags). O Mercado Livre suporta apenas plain text.
   - Use cabeçalhos simples em CAIXA ALTA (ex: SOBRE O PRODUTO, BENEFÍCIOS, etc.).
   - Mantenha a mesma estrutura básica (parágrafos e listas).

Retorne EXCLUSIVAMENTE o texto reescrito completo em português, sem comentários ou markdown."""

def obter_chave_conteudo(texto: str) -> str:
    # Remove a seção O QUE VEM NO KIT e quantidades para agrupar kits iguais no cache
    linhas = texto.splitlines()
    linhas_filtradas = []
    ignorar = False
    for l in linhas:
        l_upper = l.strip().upper()
        if l_upper.startswith("O QUE VEM NO KIT") or l_upper.startswith("ITENS INCLUSOS"):
            ignorar = True
            continue
        if ignorar and (l.strip().startswith("-") or l.strip().startswith("*") or l.strip().startswith("x")):
            continue
        if ignorar and l.strip() == "":
            continue
        ignorar = False
        linhas_filtradas.append(l)
    return "\n".join(linhas_filtradas).strip()

def extrair_secao_kit(texto: str) -> str:
    # Extrai o trecho original O QUE VEM NO KIT para re-injetar
    linhas = texto.splitlines()
    linhas_kit = []
    capturando = False
    for l in linhas:
        l_upper = l.strip().upper()
        if l_upper.startswith("O QUE VEM NO KIT") or l_upper.startswith("ITENS INCLUSOS"):
            capturando = True
            linhas_kit.append(l)
            continue
        if capturando:
            if l.strip().startswith("-") or l.strip().startswith("*") or l.strip().startswith("x") or l.strip() == "":
                linhas_kit.append(l)
            else:
                break
    return "\n".join(linhas_kit).strip()

def corrigir_programatico(texto: str) -> tuple[str, list[str]]:
    # Limpa termos subjetivos/comerciais de descrições que não são de saúde
    substituicoes = {
        # ── Adjetivos subjetivos de qualidade ─────────────────────────────
        r'\b[rR]esistente\b': 'durável',
        r'\b[rR]esistência\b': 'durabilidade',
        r'\b[rR]esistencia\b': 'durabilidade',
        r'\b[pP]rático\b': 'funcional',
        r'\b[pP]ratico\b': 'funcional',
        r'\b[pP]raticidade\b': 'funcionalidade',
        r'\b[cC]ompleto\b': 'especificado',
        r'\b[cC]ompletamente\b': 'totalmente',
        r'\b[eE]xcelente\b': 'adequado',
        r'\b[pP]remium\b': 'selecionado',
        r'\b[cC]lássico\b': 'tradicional',
        r'\b[cC]lassico\b': 'tradicional',
        r'\b[oO]riginal\b': 'genuíno',
        r'\b[sS]uperior\b': 'avançado',
        r'\b[eE]xclusivo\b': 'específico',
        r'\b[eE]xcepcional\b': 'adequado',
        r'\b[iI]ncrível\b': 'notável',
        r'\b[iI]ncrivel\b': 'notável',
        r'[Aa]lta [Qq]ualidade': 'alta especificação',
        r'[Ó|ó]tima [Qq]ualidade': 'boa especificação',
        r'[Oo]tima [Qq]ualidade': 'boa especificação',
        r'[Qq]ualidade [Ss]uperior': 'especificação avançada',
        r'\b[tT]op de [Ll]inha\b': 'linha especializada',
        r'\b[lL]inha [Pp]remium\b': 'linha especializada',
        r'\b[sS]ofisticado\b': 'elaborado',
        r'\b[eE]legante\b': 'refinado',
        r'\b[iI]novador\b': 'moderno',
        r'\b[dD]iferenciado\b': 'específico',
        r'\b[lL]uxuoso\b': 'refinado',
        r'\b[eE]spetacular\b': 'notável',
        r'\b[sS]urpreendente\b': 'notável',
        r'\b[eE]xtraordinário\b': 'notável',
        r'\b[eE]xtraordinario\b': 'notável',
        r'\b[mM]agnífico\b': 'notável',
        r'\b[mM]agnifico\b': 'notável',
        # ── Termos comerciais e de promoção ───────────────────────────────
        r'\b[oO]ferta\b': 'produto',
        r'\b[pP]romoção\b': 'produto',
        r'\b[pP]romocao\b': 'produto',
        r'\b[dD]esconto\b': 'produto',
        r'\b[iI]mperdível\b': 'produto',
        r'\b[iI]mperdivel\b': 'produto',
        r'\b[lL]ançamento\b': 'produto',
        r'\b[lL]ancamento\b': 'produto',
        r'\b[nN]ovidade\b': 'produto',
        r'[Ee]stoque [Ll]imitado': 'disponível em estoque',
        r'[Ss]uper [Oo]ferta': 'produto',
        r'[Mm]ega [Oo]ferta': 'produto',
        # ── Promessas de confiança / garantia ────────────────────────────
        r'\b[gG]arantia\b': 'suporte',
        r'\b[gG]arantido\b': 'assegurado',
        r'\b[gG]arantimos\b': 'asseguramos',
        r'[cC]om [Gg]arantia': 'com suporte',
        r'[sS]atisfação [Gg]arantida': 'satisfação assegurada',
        r'[sS]atisfacao [Gg]arantida': 'satisfação assegurada',
        r'\b[cC]ertificado\b': 'especificado',
        r'\b[tT]estado\b': 'verificado',
        # ── Promessas de logística / entrega ─────────────────────────────
        r'\b[eE]nvio [rR]ápido\b': 'despacho',
        r'\b[eE]nvio [rR]apido\b': 'despacho',
        r'\b[eE]nvio [iI]mediato\b': 'despacho',
        r'\b[eE]nvio [hH]oje\b': 'despacho',
        r'\b[pP]ronta [eE]ntrega\b': 'disponibilidade',
        r'\b[eE]ntrega [rR]ápida\b': 'despacho',
        r'\b[eE]ntrega [rR]apida\b': 'despacho',
        r'\b[eE]ntrega [iI]mediata\b': 'despacho',
        r'\b[dD]espacho [iI]mediato\b': 'despacho',
        r'\b[dD]espacho [rR]ápido\b': 'despacho',
        r'\b[dD]espacho [rR]apido\b': 'despacho',
        r'[fF]rete [gG]rátis': 'frete incluso',
        r'[fF]rete [gG]ratis': 'frete incluso',
        # ── Termos vazios / genéricos / filler ────────────────────────────
        r'\b[uU]so [dD]iário\b': 'uso regular',
        r'\b[uU]so [dD]iario\b': 'uso regular',
        r'\b[pP]ara [cC]asa\b': 'uso residencial',
        r'\b[dD]urável\b': 'robusto',
        r'\b[dD]uravel\b': 'robusto',
        r'\b[mM]oderno\b': 'atual',
        r'\b[vV]ersátil\b': 'multifuncional',
        r'\b[vV]ersatil\b': 'multifuncional',
        r'[cC]uidado [dD]iário': 'cuidado regular',
        r'[cC]uidado [dD]iario': 'cuidado regular',
        r'[dD]ia a [dD]ia': 'rotina',
    }

    termos_alterados = []
    texto_corrigido = texto
    
    for padrao, subst in substituicoes.items():
        compiled = re.compile(padrao)
        if compiled.search(texto_corrigido):
            termos_alterados.append(padrao.replace(r'\b', '').replace('[', '').replace(']', ''))
            texto_corrigido = compiled.sub(subst, texto_corrigido)
            
    return texto_corrigido, termos_alterados

class Command(BaseCommand):
    help = "Varre o histórico de kits e corrige descrições que contêm termos proibidos ou incompatíveis com as regras da ANVISA e do Mercado Livre."

    def add_arguments(self, parser):
        parser.add_argument(
            "--commit",
            action="store_true",
            help="Efetiva as alterações de descrição diretamente no BaseLinker.",
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
        gemini = GeminiClient()

        query = HistoricoKit.objects.all()
        if sku_filtro:
            query = query.filter(sku_kit=sku_filtro)

        historicos = list(query)
        total = len(historicos)
        
        # Mapeamento em memória para evitar queries individuais durante o loop
        gtin_map = dict(GtinKit.objects.values_list("sku_kit", "quantidade"))
        close_old_connections()
        
        self.stdout.write(self.style.WARNING(f"Encontrados {total} registros de histórico para analisar descrições..."))

        alterados = 0
        erros = 0
        lote_tamanho = 100

        # Termos que disparam o compliance de saúde ANVISA
        termos_gatilho_anvisa = [
            # ── Ação farmacológica ────────────────────────────────────────────
            "antiss", "anti-s", "antifung", "antivir", "antibact", "antimicr",
            "antiox", "antialerg", "antiparas", "anti-hist", "antiinflam",
            "expect", "bronco", "inflam", "analg", "sedativ", "sedant",
            "cicatriz", "diuret", "laxant", "laxativ",
            "terapêut", "terapeut", "propriedades terapêut",
            # ── Doenças e sintomas ────────────────────────────────────────────
            "tosse", "gripe", "resfriado", "garganta", "muco", "congest",
            "rouquid", "catarro", "arranhado", "irritaç", "inflamaç",
            "colesterol", "diabetes", "insônia", "insonia",
            "ansiedade", "depressão", "depressao",
            "artrite", "dor nas juntas", "febre", "náusea", "nausea", "diarreia",
            # ── Cura e tratamento ─────────────────────────────────────────────
            "alívio imediato", "alivio imediato", "alivia rapidamente",
            "auxili na recuper", "recuperaç", "prevenindo novas",
            "combate a causa", "eliminaç do muco", "eliminar o muco",
            "proteção natural", "protecao natural",
            "age na", "atua no", "ação direta", "acao direta",
            # ── Prescrição / posologia ────────────────────────────────────────
            "bula", "posologia", "borrifad", "dosagem", "dose recomendada",
            "sob prescriç", "sob prescricao", "uso terapêut",
            "tratamento de", "tratamento para", "pacientes",
        ]

        cache_rescritas = {}

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
                self.stdout.write(self.style.ERROR(f"Erro ao buscar lote no BaseLinker: {e}"))
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
                    continue

                desc_atual = (raw.get("text_fields", {}) or {}).get("description") or raw.get("description") or ""
                if not desc_atual:
                    continue

                # Determina se precisa de compliance ANVISA
                desc_lower = desc_atual.lower()
                e_saude = any(tg in desc_lower for tg in termos_gatilho_anvisa)

                desc_corrigida = desc_atual
                modo_correcao = "PROGRAMATICA"
                termos_identificados = []

                if e_saude:
                    modo_correcao = "IA_GEMINI"
                    conteudo_chave = obter_chave_conteudo(desc_atual)
                    hash_conteudo = hashlib.md5(conteudo_chave.encode('utf-8')).hexdigest()

                    # Verifica se já temos a versão rescrita no cache
                    if hash_conteudo in cache_rescritas:
                        corpo_rescrito = cache_rescritas[hash_conteudo]
                    else:
                        self.stdout.write(self.style.NOTICE(f"  -> Chamando Gemini para reescrever descrição de Saúde [{sku}]..."))
                        try:
                            # Chama o modelo Gemini Pro (ou o client configurado no projeto)
                            resposta = gemini.chat(system_prompt=PROMPT_REWRITE_DESC, user_input=conteudo_chave)
                            if not resposta or "Error" in resposta:
                                raise ValueError("Resposta do Gemini inválida ou vazia.")
                            corpo_rescrito = resposta.strip().replace("```markdown", "").replace("```", "").strip()
                            cache_rescritas[hash_conteudo] = corpo_rescrito
                        except Exception as e_ai:
                            self.stdout.write(self.style.ERROR(f"  -> Erro ao chamar IA para {sku}: {e_ai}"))
                            erros += 1
                            continue

                    # Extrai o "O QUE VEM NO KIT" original para recolocar
                    bloco_kit = extrair_secao_kit(desc_atual)
                    
                    # Reconstrói a descrição com o bloco do kit reinjetado antes da seção IDEAL PARA
                    if "IDEAL PARA" in corpo_rescrito:
                        partes = corpo_rescrito.split("IDEAL PARA", 1)
                        desc_corrigida = f"{partes[0].strip()}\n\n{bloco_kit}\n\nIDEAL PARA{partes[1]}"
                    else:
                        desc_corrigida = f"{corpo_rescrito}\n\n{bloco_kit}"

                    termos_identificados = ["Regras ANVISA Compliance"]
                else:
                    # Correção rápida via expressões regulares para não-saúde
                    desc_corrigida, termos_identificados = corrigir_programatico(desc_atual)

                nome_prod = (raw.get("text_fields", {}) or {}).get("name") or raw.get("name") or ""

                # Identifica a quantidade do kit
                qtd_kit = gtin_map.get(sku) or 0
                if not qtd_kit:
                    match_kit = re.search(r'\bKit\s+(\d+)\b', nome_prod, re.IGNORECASE)
                    if match_kit:
                        qtd_kit = int(match_kit.group(1))
                if not qtd_kit:
                    bundles = raw.get("bundle_products")
                    if isinstance(bundles, dict) and bundles:
                        try:
                            qtd_kit = max(int(v) for v in bundles.values())
                        except (ValueError, TypeError):
                            pass
                if not qtd_kit or qtd_kit < 1:
                    qtd_kit = 1

                # Aplica o ajuste de quantidade na seção O QUE VEM NO KIT
                desc_ajustada = ajustar_descricao_quantidade(
                    desc_corrigida,
                    quantidade=qtd_kit,
                    nome_produto=nome_prod,
                )
                if desc_ajustada != desc_corrigida:
                    termos_identificados.append(f"Ajuste de quantidade no kit ({qtd_kit} un)")
                    desc_corrigida = desc_ajustada

                if desc_corrigida == desc_atual or not desc_corrigida:
                    continue

                self.stdout.write(self.style.WARNING(f"[{sku}] PID: {pid} ({modo_correcao})"))
                self.stdout.write(self.style.WARNING(f"  -> Termos afetados: {', '.join(termos_identificados)}"))
                
                # Visualiza apenas as primeiras 4 linhas para revisão no console
                linhas_de = [l.strip() for l in desc_atual.splitlines() if l.strip()][:3]
                linhas_para = [l.strip() for l in desc_corrigida.splitlines() if l.strip()][:3]
                self.stdout.write(self.style.WARNING(f"  -> DE:   '{' | '.join(linhas_de)}...'"))
                self.stdout.write(self.style.WARNING(f"  -> PARA: '{' | '.join(linhas_para)}...'"))

                alterados += 1

                if commit:
                    try:
                        update_payload = {
                            "inventory_id": inventory_id,
                            "product_id": pid_int,
                            "text_fields": {
                                "description": desc_corrigida,
                            }
                        }
                        client._request("addInventoryProduct", update_payload)
                        self.stdout.write(self.style.SUCCESS(f"  -> [COMMIT] Descrição atualizada com sucesso no BaseLinker."))
                    except Exception as e_up:
                        erros += 1
                        self.stdout.write(self.style.ERROR(f"  -> [COMMIT] Erro ao atualizar descrição do kit {sku}: {e_up}"))
                else:
                    self.stdout.write(self.style.NOTICE(f"  -> [DRY-RUN] Nenhuma alteração foi gravada na descrição."))

        self.stdout.write(self.style.SUCCESS(
            f"\nProcessamento concluído. Analisados: {total} | Alterados: {alterados} | Erros: {erros}."
        ))
        if not commit:
            self.stdout.write(self.style.WARNING("ATENÇÃO: Executado em modo DRY-RUN. Use o parâmetro '--commit' para gravar as alterações das descrições."))
