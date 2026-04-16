# Generated migration for get_next_kit_sku() PL/pgSQL function
# Alocação atômica: primeiro consome recycled (NumeroReciclado), depois sequência (ControleSequencia)

from django.db import migrations


FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION get_next_kit_sku()
RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_sku INTEGER;
    v_rec_id BIGINT;
BEGIN
    -- 1. Tenta consumir um número reciclado (SELECT FOR UPDATE SKIP LOCKED evita contenção)
    SELECT id, numero INTO v_rec_id, v_sku
    FROM criacao_estruturado_numeroreciclado
    ORDER BY numero ASC
    LIMIT 1
    FOR UPDATE SKIP LOCKED;

    IF v_rec_id IS NOT NULL THEN
        DELETE FROM criacao_estruturado_numeroreciclado WHERE id = v_rec_id;
        RETURN v_sku;
    END IF;

    -- 2. Se não há reciclados, usa a sequência (lock na linha singleton)
    UPDATE criacao_estruturado_controlesequencia
    SET proximo_numero = proximo_numero + 1
    WHERE nome = 'Sequencia Kits'
    RETURNING proximo_numero - 1 INTO v_sku;

    -- Fallback: cria sequência se não existir (INSERT ... ON CONFLICT)
    IF v_sku IS NULL THEN
        INSERT INTO criacao_estruturado_controlesequencia (nome, proximo_numero)
        VALUES ('Sequencia Kits', 10687)
        ON CONFLICT (nome) DO UPDATE SET proximo_numero = criacao_estruturado_controlesequencia.proximo_numero + 1
        RETURNING proximo_numero - 1 INTO v_sku;
    END IF;

    RETURN v_sku;
END;
$$;
"""

REVERSE_SQL = "DROP FUNCTION IF EXISTS get_next_kit_sku();"


class Migration(migrations.Migration):

    dependencies = [
        ('criacao_estruturado', '0004_alter_historicokit_unique_together_and_more'),
    ]

    operations = [
        migrations.RunSQL(FUNCTION_SQL, REVERSE_SQL),
    ]
