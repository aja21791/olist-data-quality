"""Fase 2: datos sintéticos exclusivos para verificar la lógica técnica.

NO contienen resultados del dataset Olist real.
"""
import csv
from pathlib import Path

import pytest

from src.model_metadata import TABLE_SPECS
from src.extract import csv_read_query


def _write(path: Path, headers: list[str], rows: list[list[str]]):
    with path.open('w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(headers)
        writer.writerows(rows)


def synthetic_olist_dataset(folder: Path):
    specs = {
        'olist_customers_dataset.csv': (['customer_id','customer_unique_id'], [['c1', 'u1'], ['c2', 'u1']]),
        'olist_orders_dataset.csv': (['order_id','customer_id','order_status','order_purchase_timestamp'],
                                     [['o1','c1','delivered','2018-01-01 12:00:00'], ['o2','c2','processing','2018-01-02 12:00:00']]),
        'olist_order_items_dataset.csv': (['order_id','order_item_id','product_id','seller_id','price','freight_value'],
                                          [['o1','1','p1','s1','001.20','0'], ['o1','2','p1','s1','2.50','1.10']]),
        'olist_order_payments_dataset.csv': (['order_id','payment_sequential','payment_type','payment_value'],
                                             [['o1','1','credit_card','2.00'], ['o1','2','voucher','2.80']]),
        'olist_order_reviews_dataset.csv': (['review_id','order_id','review_comment_message'],
                                            [['r1','o1',''], ['r1','o2','texto']]),
        'olist_products_dataset.csv': (['product_id','product_category_name'], [['p1','category_1']]),
        'olist_sellers_dataset.csv': (['seller_id','seller_zip_code_prefix'], [['s1','01234']]),
        'olist_geolocation_dataset.csv': (['geolocation_zip_code_prefix','geolocation_lat'],
                                          [['01234','-23.4'], ['01234','-23.5']]),
        'product_category_name_translation.csv': (['product_category_name','product_category_name_english'],
                                                   [['category_1','english_1']]),
    }
    assert set(specs) == {s.filename for s in TABLE_SPECS}
    folder.mkdir(parents=True, exist_ok=True)
    for filename, (headers, rows) in specs.items():
        _write(folder / filename, headers, rows)


def test_csv_query_declares_all_columns_varchar(tmp_path):
    read_sql = csv_read_query(tmp_path / 'example.csv', ['id', 'zip code'])
    assert 'auto_detect = false' in read_sql
    assert "'zip code': 'VARCHAR'" in read_sql
    assert 'force_not_null' in read_sql


def test_phase2_synthetic_raw_and_profile(tmp_path):
    duckdb = pytest.importorskip('duckdb')
    from src.extract import load_raw
    from src.understand import profile_raw
    input_dir = tmp_path / 'raw'
    synthetic_olist_dataset(input_dir)
    db_file = tmp_path / 'test.duckdb'
    initial = load_raw(input_dir=input_dir, db_path=db_file)
    assert len(initial) == 9
    assert all(row['action'] == 'LOADED' for row in initial)

    con = duckdb.connect(str(db_file))
    assert con.execute('SELECT COUNT(*) FROM raw.order_items').fetchone()[0] == 2
    assert con.execute('SELECT price FROM raw.order_items ORDER BY order_item_id LIMIT 1').fetchone()[0] == '001.20'
    assert con.execute("SELECT review_comment_message FROM raw.order_reviews WHERE order_id='o1'").fetchone()[0] == ''
    assert {x[1] for x in con.execute('DESCRIBE raw.order_items').fetchall()} == {'VARCHAR'}
    con.close()

    repeated = load_raw(input_dir=input_dir, db_path=db_file)
    assert all(row['action'] == 'SKIP_SAME_SOURCE' for row in repeated)
    result = profile_raw(db_path=db_file, reports_dir=tmp_path / 'reports', dictionary_dir=tmp_path / 'dictionary')
    by_table = {row['table']: row for row in result['raw_keys']}
    assert by_table['order_items']['status'] == 'VALID_CANDIDATE'
    assert by_table['order_payments']['status'] == 'VALID_CANDIDATE'
    assert any(row['child_table']=='orders' and row['orphan_rows']==0 for row in result['raw_references'])
    assert (tmp_path / 'dictionary' / 'olist_er.mmd').exists()

    # Cambiar un origen obliga a solicitar una recarga explícita; RAW sigue intacto.
    with (input_dir / 'olist_orders_dataset.csv').open('a', encoding='utf-8') as f:
        f.write('o3,c1,approved,2018-01-03 12:00:00\n')
    with pytest.raises(RuntimeError, match='cambió'):
        load_raw(input_dir=input_dir, db_path=db_file)
    con = duckdb.connect(str(db_file))
    assert con.execute('SELECT COUNT(*) FROM raw.orders').fetchone()[0] == 2
    con.close()
