# Migration: Trigger para inserir sku_kit em recycled (NumeroReciclado) ao deletar HistoricoKit

from django.db import migrations


TRIGGER_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION trg_historicokit_delete_to_recycled()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_numero INTEGER;
BEGIN
    BEGIN
        v_numero := OLD.sku_kit::INTEGER;
    EXCEPTION WHEN OTHERS THEN
        RETURN OLD;  -- SKU não numérico, ignora
    END;

    IF v_numero IS NOT NULL AND v_numero > 0 AND v_numero <= 29000 THEN
        INSERT INTO criacao_estruturado_numeroreciclado (numero)
        VALUES (v_numero)
        ON CONFLICT (numero) DO NOTHING;
    END IF;

    RETURN OLD;
END;
$$;
"""

TRIGGER_SQL = """
DROP TRIGGER IF EXISTS trg_historicokit_after_delete ON criacao_estruturado_historicokit;
CREATE TRIGGER trg_historicokit_after_delete
AFTER DELETE ON criacao_estruturado_historicokit
FOR EACH ROW
EXECUTE PROCEDURE trg_historicokit_delete_to_recycled();
"""

REVERSE_SQL = """
DROP TRIGGER IF EXISTS trg_historicokit_after_delete ON criacao_estruturado_historicokit;
DROP FUNCTION IF EXISTS trg_historicokit_delete_to_recycled();
"""

FORWARD_SQL = TRIGGER_FUNCTION_SQL + TRIGGER_SQL


class Migration(migrations.Migration):

    dependencies = [
        ('criacao_estruturado', '0006_add_baselinker_kit_sku'),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
