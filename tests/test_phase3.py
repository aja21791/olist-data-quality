"""Fase 3: pruebas de funciones puras y de integración con datos sintéticos.

Nunca se atribuyen sus resultados a Olist real.
"""
from pathlib import Path
import pytest

from src.diagnose import sql_from_file, percent, output_csv, diagnose_raw
from src.extract import load_raw
from test_phase2 import synthetic_olist_dataset


def test_sql_templates_keep_identifiers_quoted():
    query = sql_from_file('02_exact_duplicates.sql', table='orders')
    assert 'raw."orders"' in query
    assert 'SELECT DISTINCT *' in query
    assert not query.endswith(';')
    query = sql_from_file('07_postal.sql', table='customers', column='customer_zip_code_prefix')
    assert "'^[0-9]{5}$'" in query
    assert 'raw."customers"' in query


def test_percentage_and_csv_generation(tmp_path):
    assert percent(2,4)==50.0
    assert percent(1,0)==0.0
    path=output_csv(tmp_path,'issues',[{'rule':'OUTLIER','n':2}],['rule','n'])
    assert path.is_file()
    assert 'OUTLIER' in path.read_text(encoding='utf-8-sig')


def test_phase3_diagnosis_is_read_only_and_reproducible(tmp_path):
    duckdb = pytest.importorskip('duckdb')
    source_dir=tmp_path/'raw'
    synthetic_olist_dataset(source_dir)
    db_file=tmp_path/'sample.duckdb'
    load_raw(input_dir=source_dir, db_path=db_file)
    db_before=db_file.stat().st_size
    results=diagnose_raw(db_file, tmp_path/'reports',tmp_path/'figures',create_figures=False)
    assert len(results['overview'])==9
    assert {d['table'] for d in results['duplicates']} == {r['table'] for r in results['overview']}
    # Dos artículos y dos pagos: deben sumarse por separado, no formar 4 cruces.
    matched=[r for r in results['payment_summary'] if r['reconciliation_status']=='MATCHED']
    assert matched and matched[0]['orders']==1
    assert results['references'][0]['orphan_rows']==0
    assert (tmp_path/'reports'/'phase3_quality_report.md').is_file()
    assert (tmp_path/'reports'/'phase3_summary.json').is_file()
    assert (tmp_path/'reports'/'phase3_payment_summary.csv').is_file()
    assert db_file.stat().st_size == db_before
    repeated=diagnose_raw(db_file,tmp_path/'reports',tmp_path/'figures',create_figures=False)
    assert repeated==results
    assert db_file.stat().st_size == db_before
