# apps/core/models/historico_estruturado.py
import uuid
from django.db import models
from django.utils import timezone


class HistoricoEstruturado(models.Model):
    """
    Histórico de processamento de entidades externas (produtos, pedidos, clientes, etc.)

    Objetivo principal:
    - Garantir idempotência: o robô nunca processa duas vezes o mesmo item
    - Evitar perda de registros: nunca assume que algo já foi feito só porque existe
    - Permitir reprocessamento controlado em caso de erro
    - Fornecer auditoria completa (quem falhou, quando, por quê e quantas tentativas)

    Uso típico em robôs de integração com:
        Bling, Configuracoes, Tiny, Mercado Livre, Shopify, Tray, Nuvemshop, etc.
    """

    # ------------------------------------------------------------------ #
    #                      ESCOLHAS DE STATUS                           #
    # ------------------------------------------------------------------ #

    PROCESSAMENTO_CHOICES = [
        ('pendente', 'Pendente'),                    # Ainda não foi processado
        ('processado', 'Processado com Sucesso'),    # Tudo certo
        ('erro', 'Erro no Processamento'),           # Falhou (pode tentar novamente)
        ('ignorado', 'Ignorado'),                    # Ex: já existe, duplicado, regra de negócio
    ]

    # ------------------------------------------------------------------ #
    #                           CAMPOS                                   #
    # ------------------------------------------------------------------ #

    # PK interna segura (UUID4) - nunca exposta ao sistema externo
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text="Identificador único interno (UUID4). Nunca muda."
    )

    id_externo = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="ID original do produto/pedido no sistema externo (Bling, Configuracoes, etc.)"
    )

    entidade = models.CharField(
        max_length=50,
        db_index=True,
        help_text="Tipo da entidade sendo sincronizada. Ex: 'produto', 'pedido', 'cliente', 'nota_fiscal'"
    )

    status = models.CharField(
        max_length=20,
        choices=PROCESSAMENTO_CHOICES,
        default='pendente',
        db_index=True,
        help_text="Estado atual do processamento. NUNCA use BooleanField aqui!"
    )

    tentativa = models.PositiveSmallIntegerField(
        default=0,
        help_text="Quantas vezes o robô já tentou processar este item"
    )

    ultimo_tentativa_em = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Data/hora da última tentativa de processamento"
    )

    processado_em = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Data/hora em que foi concluído com sucesso (status = 'processado')"
    )

    mensagem = models.TextField(
        blank=True,
        default='',
        help_text="Detalhes do erro ou motivo do 'ignorado'. Ex: 'Produto já existe', 'Estoque negativo', 'Erro 500 na API'"
    )

    criado_em = models.DateTimeField(
        default=timezone.now,
        editable=False,
        db_index=True,
        help_text="Quando este registro foi criado no banco"
    )

    # ------------------------------------------------------------------ #
    #                           META                                     #
    # ------------------------------------------------------------------ #
    class Meta:
        verbose_name = "Histórico de Processamento"
        verbose_name_plural = "Históricos de Processamento"
        ordering = ["-criado_em"]

        # Índices essenciais para performance em tabelas com milhões de linhas
        indexes = [
            models.Index(fields=["entidade", "status"]),                    # Filtro principal do robô
            models.Index(fields=["id_externo"]),                            # Busca por ID externo
            models.Index(fields=["status", "ultimo_tentativa_em"]),         # Retry de erros antigos
            models.Index(fields=["entidade", "status", "tentativa"]),      # Dashboard de pendências
        ]

        # Garante que não exista duplicidade acidental
        constraints = [
            models.UniqueConstraint(fields=['id_externo', 'entidade'], name='unique_id_externo_por_entidade')
        ]

    # ------------------------------------------------------------------ #
    #                           MÉTODOS                                  #
    # ------------------------------------------------------------------ #

    def __str__(self) -> str:
        """Representação amigável do objeto."""
        return f"{self.entidade.title()} {self.id_externo} → {self.get_status_display()}"

    def marcar_como_processado(self, commit: bool = True) -> None:
        """
        Marca o registro como processado com sucesso.
        Usado quando tudo deu certo.
        """
        self.status = 'processado'
        self.processado_em = timezone.now()
        self.ultimo_tentativa_em = timezone.now()
        self.tentativa += 1
        if commit:
            self.save(update_fields=['status', 'processado_em', 'ultimo_tentativa_em', 'tentativa'])

    def marcar_com_erro(self, erro: Exception | str, commit: bool = True) -> None:
        """
        Registra falha no processamento.
        Incrementa contador de tentativas e salva mensagem de erro.
        """
        self.status = 'erro'
        self.mensagem = str(erro)[:1000]  # limita tamanho
        self.tentativa += 1
        self.ultimo_tentativa_em = timezone.now()
        if commit:
            self.save(update_fields=['status', 'mensagem', 'tentativa', 'ultimo_tentativa_em'])

    def marcar_como_ignorado(self, motivo: str = "Duplicado ou já existente", commit: bool = True) -> None:
        """
        Usado quando o item não precisa ser processado (ex: produto já existe).
        """
        self.status = 'ignorado'
        self.mensagem = motivo[:1000]
        self.ultimo_tentativa_em = timezone.now()
        self.tentativa += 1
        if commit:
            self.save(update_fields=['status', 'mensagem', 'tentativa', 'ultimo_tentativa_em'])

    # ------------------------------------------------------------------ #
    #                       PROPRIEDADES ÚTEIS                           #
    # ------------------------------------------------------------------ #

    @property
    def deve_tentar_novamente(self) -> bool:
        """Retorna True se ainda pode tentar processar novamente (máx 5 tentativas)."""
        return self.tentativa < 5 and self.status in ['pendente', 'erro']

    @property
    def status_cor(self) -> str:
        """Para uso em admin ou relatórios (verde, vermelho, amarelo, cinza)."""
        cores = {
            'processado': 'green',
            'pendente': 'orange',
            'erro': 'red',
            'ignorado': 'gray',
        }
        return cores.get(self.status, 'black')