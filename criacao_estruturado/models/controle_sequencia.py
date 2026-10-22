# models/controle_sequencia.py
from django.db import models

class ControleSequencia(models.Model):
    """
    Controla o próximo número de sequência para SKUs de kits gerados.
    Inicia a partir de 50000.
    """
    nome = models.CharField(max_length=50, unique=True, default='Sequencia Kits')
    proximo_numero = models.PositiveIntegerField(default=50000)  # Início a partir de 50000

    class Meta:
        verbose_name = "Controle de Sequência"
        verbose_name_plural = "Controles de Sequência"

    def __str__(self) -> str:
        return f"Próximo SKU: {self.proximo_numero}"

    @classmethod
    def get_sequencia(cls) -> "ControleSequencia":
        """Retorna o singleton de sequência ou cria se inexistente."""
        obj, _ = cls.objects.get_or_create(
            nome='Sequencia Kits',
            defaults={'proximo_numero': 50000}
        )
        return obj

    def avancar(self) -> int:
        """Incrementa e retorna o número alocado."""
        numero = self.proximo_numero
        self.proximo_numero += 1
        self.save()
        return numero