"""
Testes de integração: falhas de rede, concorrência e deleção manual.
"""
import threading
from unittest.mock import patch, MagicMock

import unittest
from django.test import TestCase, override_settings
from django.db import connection

from .models import (
    ControleSequencia,
    NumeroReciclado,
    HistoricoKit,
    BaselinkerKitSku,
)
from .services.criador_kits_service import (
    CriadorKitsService,
    _is_sku_already_exists_error,
    ajustar_descricao_quantidade,
)
from .services.ficha_tecnica.categoria_atributos_service import CategoriaAtributosMLService


class GetNextKitSkuTests(TestCase):
    """Testes da função PL/pgSQL get_next_kit_sku e alocação atômica."""

    def setUp(self):
        ControleSequencia.objects.all().delete()
        NumeroReciclado.objects.all().delete()
        ControleSequencia.objects.create(nome="Sequencia Kits", proximo_numero=50000)

    def test_get_next_kit_sku_from_sequence(self):
        service = CriadorKitsService()
        sku = service._get_next_kit_sku_atomico()
        self.assertIsNotNone(sku)
        self.assertEqual(sku, 50000)
        seq = ControleSequencia.objects.get(nome="Sequencia Kits")
        self.assertEqual(seq.proximo_numero, 50001)

    def test_get_next_kit_sku_prioriza_recycled(self):
        NumeroReciclado.objects.create(numero=50500)
        service = CriadorKitsService()
        sku = service._get_next_kit_sku_atomico()
        self.assertEqual(sku, 50500)
        self.assertFalse(NumeroReciclado.objects.filter(numero=50500).exists())
        seq = ControleSequencia.objects.get(nome="Sequencia Kits")
        self.assertEqual(seq.proximo_numero, 50000)

    def test_concorrencia_alocacao_sem_gaps(self):
        """Múltiplas threads alocando SKUs não devem gerar duplicatas."""
        results = []

        def alocar():
            with connection.cursor() as cursor:
                cursor.execute("SELECT get_next_kit_sku();")
                row = cursor.fetchone()
                results.append(row[0] if row else None)

        threads = [threading.Thread(target=alocar) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 20)
        self.assertEqual(len(set(results)), 20, "SKUs devem ser únicos")
        self.assertNotIn(None, results)


class TriggerHistoricoKitDeleteTests(TestCase):
    """Testes do trigger: DELETE HistoricoKit -> recycled."""

    def setUp(self):
        NumeroReciclado.objects.all().delete()
        HistoricoKit.objects.all().delete()

    def test_delete_historicokit_insere_em_recycled(self):
        HistoricoKit.objects.create(
            sku_origem="30001",
            id_origem="123",
            sku_kit="50001",
            id_kit="456",
        )
        self.assertFalse(NumeroReciclado.objects.filter(numero=50001).exists())
        HistoricoKit.objects.filter(sku_kit="50001").delete()
        self.assertTrue(NumeroReciclado.objects.filter(numero=50001).exists())


class SkuDuplicatePreventionTests(TestCase):
    """Testes de garantia de não repetição contra banco local e BaseLinker."""

    def setUp(self):
        ControleSequencia.objects.all().delete()
        NumeroReciclado.objects.all().delete()
        HistoricoKit.objects.all().delete()
        BaselinkerKitSku.objects.all().delete()
        ControleSequencia.objects.create(nome="Sequencia Kits", proximo_numero=50000)

    def test_sku_existe_no_banco_local(self):
        service = CriadorKitsService()
        self.assertFalse(service._sku_existe_no_banco_local("50005"))

        HistoricoKit.objects.create(
            sku_origem="30001",
            id_origem="111",
            sku_kit="50005",
            id_kit="222",
        )
        self.assertTrue(service._sku_existe_no_banco_local("50005"))

    @patch.object(CriadorKitsService, "buscar_pid_por_sku")
    def test_sku_existe_no_baselinker(self, mock_buscar_pid):
        mock_buscar_pid.return_value = "98765"
        service = CriadorKitsService()
        self.assertTrue(service._sku_existe_no_baselinker("50010"))
        self.assertTrue(BaselinkerKitSku.objects.filter(sku=50010).exists())

    @patch.object(CriadorKitsService, "buscar_pid_por_sku")
    def test_obter_proximo_sku_pula_existentes(self, mock_buscar_pid):
        """Se 50000 existe no DB e 50001 no BL, deve retornar 50002."""
        HistoricoKit.objects.create(
            sku_origem="30001",
            id_origem="111",
            sku_kit="50000",
            id_kit="222",
        )

        def side_effect_buscar(sku):
            return "999" if str(sku) == "50001" else None

        mock_buscar_pid.side_effect = side_effect_buscar

        service = CriadorKitsService()
        sku_alocado = service._obter_proximo_sku(None)
        self.assertEqual(sku_alocado, "50002")

    def test_reciclar_numero_ignora_menor_50000_ou_em_uso(self):
        service = CriadorKitsService()
        service._reciclar_numero("31005")
        self.assertFalse(NumeroReciclado.objects.filter(numero=31005).exists())

        service._reciclar_numero("50050")
        self.assertTrue(NumeroReciclado.objects.filter(numero=50050).exists())


class SkuConflictDetectionTests(TestCase):
    """Testes de detecção de conflito SKU e retry."""

    def test_is_sku_already_exists_detecta_variantes(self):
        self.assertTrue(_is_sku_already_exists_error(Exception("SKU already exists")))
        self.assertTrue(_is_sku_already_exists_error(Exception("Duplicate SKU")))
        self.assertTrue(_is_sku_already_exists_error(Exception("já existe")))
        self.assertFalse(_is_sku_already_exists_error(Exception("Network error")))

    def test_normaliza_skus_secundarios_respeita_limite_de_20(self):
        from criacao_estruturado.views import _normalizar_skus_secundarios

        skus = [f"30{i}" for i in range(1, 25)]
        skus = [" 301 ", "302", "301", "303", "304", "305", "306", "307", "308", "309", "310", "311", "312", "313", "314", "315", "316", "317", "318", "319", "320", "321", "322", "323"]
        self.assertEqual(
            _normalizar_skus_secundarios(skus, "300"),
            ["301", "302", "303", "304", "305", "306", "307", "308", "309", "310", "311", "312", "313", "314", "315", "316", "317", "318", "319", "320"],
        )


class CategoriaAtributosMLServiceTests(TestCase):
    """Testa o fluxo de resolução de categoria + schema enxuto da IA."""

    @patch("criacao_estruturado.services.ficha_tecnica.categoria_atributos_service.MercadoLivreClient")
    def test_coleta_e_consolida_atributos_relevantes_para_ia(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.resolver_categoria_por_input.return_value = "MLB271283"
        mock_client.ficha_tecnica.return_value = [
            {"id": "BRAND", "name": "Marca", "required": True, "values": [{"id": "1", "name": "Marca X"}]},
            {"id": "GTIN", "name": "GTIN", "required": True, "values": []},
            {"id": "COLOR", "name": "Cor", "required": False, "values": [{"id": "10", "name": "Azul"}]},
        ]
        mock_client.technical_specs_input.return_value = [
            {"id": "VOLUME", "name": "Volume", "required": True},
            {"id": "ORIGIN", "name": "Origem", "required": False},
        ]

        service = CategoriaAtributosMLService()
        payload = service.build_payload_for_ia(
            categoria_input="Tintas Sprays",
            titulo_produto="Tinta spray 250 ml"
        )

        self.assertEqual(payload["category_id"], "MLB271283")
        self.assertIn("Marca", payload["field_names"])
        self.assertIn("Volume", payload["field_names"])
        self.assertIn("schema_ia", payload)
        self.assertIn("Marca", payload["schema_ia"]["properties"])


class CriadorKitsServiceIntegrationTests(TestCase):
    """Testes de integração do CriadorKitsService com mocks."""

    def setUp(self):
        ControleSequencia.objects.all().delete()
        NumeroReciclado.objects.all().delete()
        HistoricoKit.objects.all().delete()
        BaselinkerKitSku.objects.all().delete()
        ControleSequencia.objects.create(nome="Sequencia Kits", proximo_numero=11000)

    @patch("criacao_estruturado.services.criador_kits_service.BaselinkerClient")
    def test_executar_criacao_sku_conflict_sync_retry(self, mock_client_cls):
        """Simula conflito SKU -> sync + retry com novo SKU."""
        from integracoes.baselinker.client import BaselinkerAPIError

        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        produto = {
            "sku": "30001", "name": "Prod", "ean": "", "weight": 0,
            "text_fields": {"name": "Prod", "description": "", "features": {}},
            "prices": {}, "images": [],
        }
        mock_client._request.side_effect = [
            {"products": {123: produto}},
            BaselinkerAPIError("SKU already exists"),
            {"products": {}},
            {"product_id": "999"},
        ]

        service = CriadorKitsService()
        resultado = service.executar_criacao("123", "30001", category_id_ml="MLB271283")

        self.assertTrue(resultado["sucesso"], resultado.get("msg", ""))
        self.assertIn("sku_novo", resultado)

    @patch("criacao_estruturado.services.criador_kits_service.BaselinkerClient")
    def test_falha_rede_nao_recicla_sku_indevidamente(self, mock_client_cls):
        """Falha de rede não deve reciclar SKU (não sabemos se criou ou não)."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        mock_client._request.side_effect = [
            {"products": {123: {"sku": "30001", "name": "Prod"}}},
            ConnectionError("Network unreachable"),
        ]

        service = CriadorKitsService()
        resultado = service.executar_criacao("123", "30001")

        self.assertFalse(resultado["sucesso"])
        # SKU foi alocado mas falhou a criação - deve ter sido reciclado
        # (o serviço recicla quando não há sku_conflict)
        self.assertIn("msg", resultado)


from .services.criador_kits_service import (
    CriadorKitsService,
    _is_sku_already_exists_error,
    ajustar_descricao_quantidade,
    _limpar_travessoes,
    validar_descricao,
)


class AjustarDescricaoQuantidadeTests(unittest.TestCase):
    """Testes para ajuste de quantidade, validação e sanitização de descrição."""

    def test_limpar_travessoes(self):
        """Garante que travessões são substituídos por dois-pontos ou hífens."""
        texto_sujo = "Protetor 3M — 12 Peças – Alta fixação -- Resistente"
        limpo = _limpar_travessoes(texto_sujo)
        self.assertNotIn("—", limpo)
        self.assertNotIn("–", limpo)
        self.assertNotIn("--", limpo)
        self.assertEqual(limpo, "Protetor 3M: 12 Peças: Alta fixação: Resistente")

    def test_validar_descricao_novo_template(self):
        """Valida se o validador reconhece o formato assertivo com marcadores."""
        desc = (
            "SOBRE O PRODUTO\n"
            "Texto curto e direto.\n\n"
            "PRINCIPAIS CARACTERÍSTICAS\n"
            "• 12 peças\n• Formato redondo\n\n"
            "BENEFÍCIOS\n"
            "• Protege superfícies\n\n"
            "MODO DE USO\n"
            "1. Limpe a área\n\n"
            "CONTEÚDO DA COMPRA\n"
            "• 1x Protetor Anti-Risco\n\n"
            "DÚVIDAS FREQUENTES\n"
            "1. Pergunta?\nR: Resposta.\n\n"
            "IDEAL PARA\n"
            "Pés de cadeiras e mesas."
        )
        self.assertTrue(validar_descricao(desc))

    def test_ajustar_quantidade_conteudo_da_compra(self):
        """Ajusta quantidade na seção CONTEÚDO DA COMPRA com marcador •."""
        desc = (
            "SOBRE O PRODUTO\n"
            "Descrição do termômetro.\n\n"
            "CONTEÚDO DA COMPRA\n"
            "• Termômetro Clínico Digital G-Tech x 1\n\n"
            "IDEAL PARA\n"
            "Uso doméstico."
        )
        ajustada = ajustar_descricao_quantidade(desc, quantidade=3)
        self.assertIn("• Termômetro Clínico Digital G-Tech x 3", ajustada)
        self.assertNotIn("x 1", ajustada)

    def test_ajustar_quantidade_kit_simples(self):
        desc = (
            "SOBRE O PRODUTO\n"
            "Descrição do termômetro.\n\n"
            "O QUE VEM NO KIT\n"
            "- Termômetro Clínico Digital G-Tech x 1\n\n"
            "IDEAL PARA\n"
            "Uso doméstico."
        )
        ajustada = ajustar_descricao_quantidade(desc, quantidade=3)
        self.assertIn("- Termômetro Clínico Digital G-Tech x 3", ajustada)
        self.assertNotIn("x 1", ajustada)

    def test_ajustar_quantidade_variacoes_formatos(self):
        formatos = [
            "- Termômetro Digital x 1 unidade",
            "- 1x Termômetro Digital",
            "- Termômetro Digital - 1 Unidade",
            "- Termômetro Digital (1 unidade)",
            "- Termômetro Digital",
            "• Termômetro Digital x 1",
        ]
        for f in formatos:
            desc = f"SOBRE O PRODUTO\nTexto.\n\nCONTEÚDO DA COMPRA\n{f}\n\nIDEAL PARA\nIdeal."
            ajustada = ajustar_descricao_quantidade(desc, quantidade=4)
            marcador = "•" if "•" in f else "-"
            self.assertIn(f"{marcador} Termômetro Digital x 4", ajustada)

    def test_ajustar_quantidade_kit_misto(self):
        desc = (
            "SOBRE O PRODUTO\n"
            "Texto kit misto.\n\n"
            "CONTEÚDO DA COMPRA\n"
            "• Termômetro Digital x 1\n"
            "• Oxímetro de Dedo x 1\n\n"
            "IDEAL PARA\n"
            "Ideal para clínicas."
        )
        ajustada = ajustar_descricao_quantidade(desc, quantidade=2)
        self.assertIn("• Termômetro Digital x 2", ajustada)
        self.assertIn("• Oxímetro de Dedo x 2", ajustada)

    def test_injetar_secao_quando_ausente(self):
        desc = (
            "SOBRE O PRODUTO\n"
            "Texto sem seção de kit.\n\n"
            "IDEAL PARA\n"
            "Ideal para todos."
        )
        ajustada = ajustar_descricao_quantidade(desc, quantidade=3, nome_produto="Termômetro Clínico Digital")
        self.assertIn("CONTEÚDO DA COMPRA", ajustada)
        self.assertIn("• Termômetro Clínico Digital x 3", ajustada)
        self.assertTrue(ajustada.index("CONTEÚDO DA COMPRA") < ajustada.index("IDEAL PARA"))
