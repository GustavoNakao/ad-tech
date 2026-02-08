# categorias/services/categoria_servico.py

import logging
from typing import Dict, Any, List
from django.utils import timezone

from baselinker_integracao.client import BaselinkerClient
from ..models.categoria_baselinker import CategoriaBaselinker

logger = logging.getLogger(__name__)

class CategoriaServico:
    @staticmethod
    def sincronizar_todas(inventory_id: int = 24542) -> Dict[str, Any]:
        client = BaselinkerClient()

        try:
            resposta = client._request("getInventoryCategories", {"inventory_id": inventory_id})
        except Exception as e:
            logger.exception("Erro ao conectar com BaseLinker")
            return {"sucesso": False, "total_api": 0, "criadas": 0, "atualizadas": 0, "erros": 1}

        raw_categories = resposta.get("categories", [])
        if isinstance(raw_categories, dict):
            categorias_lista = [{"category_id": int(k), **v} for k, v in raw_categories.items()]
        elif isinstance(raw_categories, list):
            categorias_lista = raw_categories
        else:
            categorias_lista = []

        total = len(categorias_lista)
        criadas = atualizadas = erros = 0

        # PASSO 1: Criar/atualizar todas as categorias SEM definir o parent ainda
        for cat in categorias_lista:
            try:
                category_id = int(cat["category_id"])
                nome = cat.get("name", f"Categoria {category_id}").strip()

                obj, criada = CategoriaBaselinker.objects.update_or_create(
                    category_id=category_id,
                    defaults={
                        "name": nome,
                        "inventory_id": inventory_id,
                        "ativo": True,
                        "sincronizado_em": timezone.now(),
                        # parent deixado como None por enquanto
                    }
                )
                if criada:
                    criadas += 1
                else:
                    atualizadas += 1
            except Exception as e:
                erros += 1
                logger.error(f"Erro na criação da categoria {cat.get('category_id')}: {e}")

        # PASSO 2: Agora sim, preencher os relacionamentos de parentesco
        erros_parent = 0
        for cat in categorias_lista:
            try:
                category_id = int(cat["category_id"])
                parent_id_api = cat.get("parent_id")

                if parent_id_api and parent_id_api != 0:
                    try:
                        parent_obj = CategoriaBaselinker.objects.get(category_id=parent_id_api)
                        CategoriaBaselinker.objects.filter(category_id=category_id).update(parent=parent_obj)
                    except CategoriaBaselinker.DoesNotExist:
                        logger.warning(f"Pai não encontrado: category_id={parent_id_api} (filha {category_id})")
                        erros_parent += 1
            except Exception as e:
                erros_parent += 1
                logger.error(f"Erro ao linkar pai da categoria {cat.get('category_id')}: {e}")

        total_erros = erros + erros_parent

        logger.info(f"Sincronização FINALIZADA → Inventário {inventory_id} | Total: {total} | +{criadas} criadas | ~{atualizadas} atualizadas | {total_erros} erros no parent")

        return {
            "sucesso": True,
            "total_api": total,
            "criadas": criadas,
            "atualizadas": atualizadas,
            "erros": total_erros,
        }