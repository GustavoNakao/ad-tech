# models/numero_reciclado.py
from django.db import models

class NumeroReciclado(models.Model):
    numero = models.PositiveIntegerField(unique=True)

    class Meta:
        ordering = ['numero']

    def __str__(self):
        return str(self.numero)