# models/controle_sequencia.py
from django.db import models

class ControleSequencia(models.Model):
    nome = models.CharField(max_length=50, unique=True, default='Sequencia Kits')
    proximo_numero = models.PositiveIntegerField(default=10686)  # Início no próximo desejado

    class Meta:
        verbose_name = "Controle de Sequência"
        verbose_name_plural = "Controles de Sequência"

    def __str__(self):
        return f"Próximo SKU: {self.proximo_numero}"

    @classmethod
    def get_sequencia(cls):
        obj, created = cls.objects.get_or_create(
            nome='Sequencia Kits',
            defaults={'proximo_numero': 10686}
        )
        return obj

    def avancar(self):
        numero = self.proximo_numero
        self.proximo_numero += 1
        self.save()
        return numero