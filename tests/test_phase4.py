"""Pruebas aisladas con datos sintéticos, nunca confundir con Olist real."""
from pathlib import Path

import pytest

from src.transform_schema import SCHEMA, KEYS, REQUIRED
from src.transform import transform_data, RULES


def test_rule_catalog_has_fifteen_entries():
    assert len(RULES) == 15
    assert [x[0] for x in RULES] == [f'R{i:02d}' for i in range(1,16)]


def test_required_fields_belong_to_actual_table_model():
    for table, keys in KEYS.items():
        assert set(keys).issubset(SCHEMA[table])
    for table, fields in REQUIRED.items():
        assert set(fields).issubset(SCHEMA[table])


@pytest.fixture()
def synthetic_db(tmp_path):
    duckdb = pytest.importorskip('duckdb', reason='Instalar dependencias en olist-quality')
    db_path = tmp_path / 'synthetic.duckdb'
    con = duckdb.connect(str(db_path))
    con.execute('CREATE SCHEMA raw')
    fixtures = {
        'customers': [
            dict(customer_id='c1', customer_unique_id='u1', customer_zip_code_prefix='1234',
                 customer_city='  São   Paulo  ', customer_state='sp'),
        ],
        'orders': [
            dict(order_id='o1', customer_id='c1', order_status=' DELIVERED ',
                 order_purchase_timestamp='2017-01-01 10:00:00',
                 order_approved_at='2017-01-01 12:00:00',
                 order_delivered_carrier_date='2017-01-02 10:00:00',
                 order_delivered_customer_date='2017-01-03 10:00:00',
                 order_estimated_delivery_date='2017-01-05 10:00:00'),
        ],
        'order_items': [
            dict(order_id='o1', order_item_id='1', product_id='p1', seller_id='s1',
                 shipping_limit_date='2017-01-02 16:00:00', price='10.00', freight_value='2.00'),
            dict(order_id='o1', order_item_id='2', product_id='p1', seller_id='s1',
                 shipping_limit_date='2017-01-02 16:00:00', price='5.00', freight_value='1.00'),
            dict(order_id='o1', order_item_id='3', product_id='p1', seller_id='s1',
                 shipping_limit_date='2017-01-02 16:00:00', price='-3.00', freight_value='1.00'),
        ],
        'order_payments': [
            dict(order_id='o1', payment_sequential='1', payment_type=' CREDIT_CARD ',
                 payment_installments='1', payment_value='12.00'),
            dict(order_id='o1', payment_sequential='2', payment_type='boleto',
                 payment_installments='1', payment_value='6.00'),
        ],
        'order_reviews': [
            dict(review_id='r1', order_id='o1', review_score='5',
                 review_comment_title='', review_comment_message='',
                 review_creation_date='2017-01-04 00:00:00',
                 review_answer_timestamp='2017-01-05 00:00:00'),
        ],
        'products': [
            dict(product_id='p1', product_category_name=' BELEZA_SAUDE ',
                 product_name_lenght='50', product_description_lenght='200',
                 product_photos_qty='1', product_weight_g='100',
                 product_length_cm='10', product_height_cm='5', product_width_cm='5'),
        ],
        'sellers': [dict(seller_id='s1', seller_zip_code_prefix='05470',
                         seller_city=' SAO PAULO ', seller_state='SP')],
        'geolocation': [dict(geolocation_zip_code_prefix='05470', geolocation_lat='-23.5',
                             geolocation_lng='-46.5', geolocation_city='São Paulo',
                             geolocation_state='sp')],
        'category_translation': [dict(product_category_name='beleza_saude',
                                      product_category_name_english='health_beauty')],
    }
    # Repeticiones legítimas y una repetición exacta de un pedido que sí tiene clave única.
    fixtures['orders'].append(fixtures['orders'][0].copy())
    fixtures['order_reviews'].append(fixtures['order_reviews'][0].copy())
    fixtures['geolocation'].append(fixtures['geolocation'][0].copy())
    for table, fields in SCHEMA.items():
        con.execute(f'CREATE TABLE raw."{table}" (' + ','.join(f'"{f}" VARCHAR' for f in fields)+')')
        con.executemany('INSERT INTO raw."'+table+'" VALUES ('+','.join('?' for _ in fields)+')',
                        [[row.get(f, '') for f in fields] for row in fixtures[table]])
    con.close()
    return db_path


def test_phase4_preserves_legitimate_multiple_items_payments_and_raw(synthetic_db, tmp_path):
    import duckdb
    con = duckdb.connect(str(synthetic_db), read_only=True)
    before = {table: con.execute(f'SELECT COUNT(*) FROM raw."{table}"').fetchone()[0] for table in SCHEMA}
    con.close()
    result = transform_data(synthetic_db, tmp_path/'reports')
    assert result['status']=='COMPLETED'
    con = duckdb.connect(str(synthetic_db), read_only=True)
    try:
        after = {table: con.execute(f'SELECT COUNT(*) FROM raw."{table}"').fetchone()[0] for table in SCHEMA}
        assert before == after
        assert con.execute('SELECT COUNT(*) FROM clean.orders').fetchone()[0]==1
        assert con.execute('SELECT COUNT(*) FROM clean.order_items').fetchone()[0]==2
        assert con.execute('SELECT COUNT(*) FROM clean.order_payments').fetchone()[0]==2
        assert con.execute('SELECT COUNT(*) FROM clean.geolocation').fetchone()[0]==2
        assert con.execute('SELECT COUNT(*) FROM clean.order_reviews').fetchone()[0]==2
        assert con.execute('SELECT order_status FROM clean.orders').fetchone()[0]=='delivered'
        assert con.execute('SELECT customer_zip_code_prefix FROM clean.customers').fetchone()[0]=='01234'
        assert con.execute('SELECT COUNT(*) FROM audit.quarantine_records WHERE reason=\'INVALID_DOMAIN\'').fetchone()[0]==1
        assert con.execute('SELECT COUNT(*) FROM audit.quarantine_records WHERE reason=\'REDUNDANT_KEY_ROW\'').fetchone()[0]==1
        assert con.execute('SELECT COUNT(*) FROM audit.quality_rule_catalog').fetchone()[0]==15
        assert con.execute("SELECT COUNT(*) FROM audit.field_changes WHERE source_table='customers' AND column_name='customer_zip_code_prefix'").fetchone()[0]==1
    finally:
        con.close()
    assert (tmp_path/'reports'/'phase4_transformation_report.md').is_file()


def test_phase4_repeated_execution_is_idempotent_for_clean(synthetic_db, tmp_path):
    import duckdb
    first = transform_data(synthetic_db, tmp_path/'reports')
    con=duckdb.connect(str(synthetic_db),read_only=True)
    before={t:con.execute(f'SELECT COUNT(*) FROM clean."{t}"').fetchone()[0] for t in SCHEMA}
    con.close()
    second=transform_data(synthetic_db, tmp_path/'reports')
    con=duckdb.connect(str(synthetic_db),read_only=True)
    after={t:con.execute(f'SELECT COUNT(*) FROM clean."{t}"').fetchone()[0] for t in SCHEMA}
    n=con.execute("SELECT COUNT(*) FROM audit.transform_runs WHERE status='COMPLETED'").fetchone()[0]
    con.close()
    assert before == after
    assert first['run_id'] != second['run_id'] and n==2


def test_business_key_conflict_is_not_silently_resolved(synthetic_db,tmp_path):
    import duckdb
    con=duckdb.connect(str(synthetic_db))
    con.execute("INSERT INTO raw.customers VALUES ('c1','u1','1234','Ciudad distinta','sp')")
    con.close()
    transform_data(synthetic_db, tmp_path/'reports')
    con=duckdb.connect(str(synthetic_db),read_only=True)
    try:
        assert con.execute('SELECT COUNT(*) FROM clean.customers').fetchone()[0]==0
        assert con.execute("SELECT COUNT(*) FROM audit.quarantine_records WHERE source_table='customers' AND reason='BUSINESS_KEY_CONFLICT'").fetchone()[0]==2
    finally: con.close()


def test_numeric_business_identifier_is_checked_in_original_varchar():
    """Evitar TRIM(INTEGER) cuando CLEAN convierte order_item_id desde RAW."""
    from src.transform import build_candidates

    statements = []

    class RecordingConnection:
        def execute(self, sql, *params):
            statements.append(sql)
            return self

    build_candidates(RecordingConnection(), 'order_items', 'synthetic_run')
    candidate = next(s for s in statements if 'TEMP TABLE _candidate_order_items' in s)
    assert 'TRIM(t."_src_order_item_id")' in candidate
    assert 'TRIM(t."order_item_id")' not in candidate


def test_classified_temp_table_identifiers_are_quoted_as_one_name():
    """Regresión: nunca generar _classified_"orders" en lugar de "_classified_orders"."""
    from src.transform import run_sql
    sql_statements = []

    class RecordingConnection:
        def execute(self, sql):
            sql_statements.append(sql)
            return self

        def fetchone(self):
            return (0, 0)

    for rule in ('02_nulos.sql', '03_texto.sql', '04_tipos.sql'):
        run_sql(RecordingConnection(), rule, classified_table='_classified_order_items')

    assert len(sql_statements) == 3
    for query in sql_statements:
        assert 'FROM "_classified_order_items"' in query
        assert '_classified_"order_items"' not in query


def test_clean_table_identifiers_remain_qualified():
    from src.transform import run_sql
    queries = []

    class RecordingConnection:
        def execute(self, sql):
            queries.append(sql)
            return self

        def fetchone(self):
            return (0,)

    run_sql(RecordingConnection(), '14_vacias.sql', table='order_items', column='product_id')
    assert 'FROM clean."order_items"' in queries[0]
    assert 'WHERE "product_id" IS NOT NULL' in queries[0]
