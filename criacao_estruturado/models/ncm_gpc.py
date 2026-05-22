# models/ncm_gpc.py
from django.db import models

class NcmGpc(models.Model):
    """
    Mapeamento persistente de NCM (Nomenclatura Comum do Mercosul - 8 dígitos)
    para o código GPC correspondente (Global Product Classification - 8 dígitos).
    """
    ncm = models.CharField(max_length=20, unique=True, db_index=True)
    gpc = models.CharField(max_length=20, db_index=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Mapeamento NCM para GPC"
        verbose_name_plural = "Mapeamentos NCM para GPC"

    def __str__(self):
        return f"NCM: {self.ncm} -> GPC: {self.gpc}"
