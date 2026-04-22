"""
Migration 0002 — Reescrita dos models (campos enxutos e corrigidos).

ATENÇÃO: Esta migration DROPA e RECRIA as tabelas pedido, item_pedido e
status_pedido. Execute somente em ambiente fresh ou após backup.

Se já existir dado em produção use:
  python manage.py migrate pedidos 0001  (volta ao estado anterior)
  ... ajuste conforme necessário ...
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('pedidos', '0001_initial'),
    ]

    operations = [
        # ── Drop seguro (IF EXISTS) via SQL direto ─────────────────────────────
        migrations.RunSQL(
            sql="""
                DROP TABLE IF EXISTS item_pedido CASCADE;
                DROP TABLE IF EXISTS pedido CASCADE;
                DROP TABLE IF EXISTS status_pedido CASCADE;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),

        # ── Recria status_pedido ───────────────────────────────────────────────
        migrations.CreateModel(
            name='StatusPedido',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('id_baselinker', models.IntegerField(unique=True)),
                ('nome', models.CharField(max_length=100)),
                ('ignorar_atualizacao', models.BooleanField(default=False)),
            ],
            options={
                'verbose_name': 'Status do Pedido',
                'verbose_name_plural': 'Status dos Pedidos',
                'db_table': 'status_pedido',
                'ordering': ['id_baselinker'],
            },
        ),

        # ── Recria pedido ─────────────────────────────────────────────────────
        migrations.CreateModel(
            name='Pedido',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('id_baselinker', models.BigIntegerField(unique=True, help_text='order_id da API')),
                ('id_pedido_loja', models.CharField(blank=True, max_length=100, help_text='shop_order_id')),
                ('id_pedido_externo', models.CharField(blank=True, max_length=100, help_text='external_order_id')),
                ('origem', models.CharField(blank=True, max_length=50, help_text='order_source')),
                ('id_origem', models.CharField(blank=True, max_length=50, help_text='order_source_id')),
                ('status', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='pedidos',
                    to='pedidos.statuspedido',
                )),
                ('confirmado', models.BooleanField(default=False)),
                ('data_criacao', models.DateTimeField(blank=True, null=True, help_text='date_add')),
                ('data_confirmacao', models.DateTimeField(blank=True, null=True, help_text='date_confirmed')),
                ('data_no_status', models.DateTimeField(blank=True, null=True, help_text='date_in_status')),
                ('email', models.EmailField(blank=True)),
                ('telefone', models.CharField(blank=True, max_length=30)),
                ('moeda', models.CharField(blank=True, max_length=3)),
                ('valor_pago', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('valor_frete', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('metodo_pagamento', models.CharField(blank=True, max_length=100)),
                ('metodo_entrega', models.CharField(blank=True, max_length=100)),
                ('numero_rastreio', models.CharField(blank=True, max_length=100)),
                ('entrega_nome', models.CharField(blank=True, max_length=100)),
                ('entrega_endereco', models.CharField(blank=True, max_length=255)),
                ('entrega_cep', models.CharField(blank=True, max_length=20)),
                ('entrega_cidade', models.CharField(blank=True, max_length=100)),
                ('entrega_estado', models.CharField(blank=True, max_length=50)),
                ('entrega_pais', models.CharField(blank=True, max_length=50)),
                ('nf_nome', models.CharField(blank=True, max_length=200)),
                ('nf_cnpj_cpf', models.CharField(blank=True, max_length=20)),
                ('nf_endereco', models.CharField(blank=True, max_length=255)),
                ('nf_cidade', models.CharField(blank=True, max_length=100)),
                ('nf_estado', models.CharField(blank=True, max_length=50)),
                ('nf_cep', models.CharField(blank=True, max_length=20)),
                ('comentario_comprador', models.TextField(blank=True)),
                ('comentario_vendedor', models.TextField(blank=True)),
                ('atualizado_em', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Pedido',
                'db_table': 'pedido',
                'ordering': ['-data_criacao'],
            },
        ),

        # ── Recria item_pedido ────────────────────────────────────────────────
        migrations.CreateModel(
            name='ItemPedido',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('pedido', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='itens',
                    to='pedidos.pedido',
                )),
                ('id_item_baselinker', models.BigIntegerField(unique=True, help_text='order_product_id')),
                ('id_produto_baselinker', models.CharField(blank=True, max_length=50, help_text='product_id')),
                ('id_variante', models.CharField(blank=True, max_length=50, help_text='variant_id')),
                ('nome', models.CharField(max_length=255)),
                ('sku', models.CharField(blank=True, max_length=100)),
                ('ean', models.CharField(blank=True, max_length=20)),
                ('atributos', models.CharField(blank=True, max_length=500)),
                ('quantidade', models.IntegerField(default=1)),
                ('preco_unitario', models.DecimalField(decimal_places=2, default=0, max_digits=12, help_text='price_brutto')),
                ('taxa', models.DecimalField(decimal_places=2, default=0, max_digits=6, help_text='% de imposto')),
                ('peso', models.DecimalField(decimal_places=3, default=0, max_digits=8, help_text='kg')),
            ],
            options={
                'verbose_name': 'Item do Pedido',
                'db_table': 'item_pedido',
            },
        ),
    ]
