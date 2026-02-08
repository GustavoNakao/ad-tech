# categorias/services/historico_servico.py
from typing import List
from django.db import transaction
from ..models.historico_estruturado import HistoricoEstruturado


class HistoricoServico:
    MAX_TENTATIVAS = 8

    @staticmethod
    def buscar_ids_pendentes(entidade: str, limite: int = 500) -> List[str]:
        """Usado por TODOS os robôs"""
        return list(
            HistoricoEstruturado.objects
            .filter(entidade=entidade, status__in=['pendente', 'erro'], tentativa__lt=HistoricoServico.MAX_TENTATIVAS)
            .order_by('criado_em')
            .values_list('id_externo', flat=True)[:limite]
        )

    @staticmethod
    @transaction.atomic
    def reservar_lote(entidade: str, worker: str, limite: int = 50):
        itens = list(
            HistoricoEstruturado.objects
            .select_for_update(skip_locked=True)
            .filter(entidade=entidade, status='pendente')
            .order_by('criado_em')[:limite]
        )
        if itens:
            HistoricoEstruturado.objects.filter(id__in=[i.id for i in itens]).update(
                status='processando',
                mensagem=f'Worker: {worker}'
            )
        return itens