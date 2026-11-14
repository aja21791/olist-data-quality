"""Fase 8: pruebas puras e integración opcional con DuckDB sintético."""
import json
from pathlib import Path

import pytest

from src.evaluate import (
    DOMAIN_RULES, DIMENSIONS, SCHEMA, assert_release_snapshot,
    classify_change, dictionary_description, metric, safe_rate,
    sql, summarize_dimensions,
)


def test_rates_do_not_claim_perfect_quality_for_empty_universe():
    assert safe_rate(0, 0) is None
    assert safe_rate(1, 4) == 25.0
    assert safe_rate(0, 50) == 0.0
    assert classify_change(10, 2) == 'MENOS_INCIDENCIAS'
    assert classify_change(0, 2) == 'MAS_INCIDENCIAS_REVISAR'


def test_metrics_keep_denominators_and_units():
    first=metric(dimension='temporal',metric_id='TIME.test',table='orders',column='carrier',
                 before=2,after=0,universe_before=10,universe_after=10,
                 unit='eventos',definition='Fechas')
    second=metric(dimension='temporal',metric_id='TIME.other',table='orders',column='delivery',
                  before=1,after=0,universe_before=10,universe_after=10,
                  unit='eventos',definition='Fechas')
    summary=next(row for row in summarize_dimensions([first,second]) if row['dimension']=='temporal')
    assert summary['before_issues']==3
    assert summary['universe_before']==20
    assert summary['rate_before_pct']==15.0
    assert summary['after_issues']==0
    assert first['universe_before']==10 and first['rate_before_pct']==20.0


def test_sql_evaluation_templates_exist_and_are_non_destructive():
    for script in ('01_field_missing.sql','02_key_duplicates.sql','03_predicate.sql',
                   '04_orphan_relationship.sql','05_order_chronology.sql'):
        content=(Path(__file__).resolve().parents[1]/'sql'/'evaluation'/script).read_text()
        upper=content.upper()
        assert 'SELECT' in upper
        assert not any(verb in upper for verb in ('UPDATE RAW.', 'UPDATE CLEAN.',
                                                 'DELETE FROM', 'DROP TABLE'))
    query=sql('04_orphan_relationship.sql',layer='raw',parent='"customers"',
              child='"orders"', parent_key='"customer_id"',
              child_key='"customer_id"',missing_child='c."customer_id" IS NULL')
    assert 'NOT EXISTS' in query
    assert 'raw."customers"' in query


def test_dictionary_does_not_equate_customer_unique_and_customer_id():
    assert 'no equivale' in dictionary_description('customers','customer_unique_id')
    assert 'R05' in dictionary_description('orders','order_delivered_carrier_date')
    assert len(SCHEMA)==9
    assert set(DIMENSIONS)=={'completeness','uniqueness','monetary','domain','temporal','referential'}
    assert any(t=='geolocation' for t,c,d in DOMAIN_RULES)


def synthetic_release(tmp_path):
    duckdb=pytest.importorskip('duckdb',reason='Requiere DuckDB instalado')
    from test_phase4 import synthetic_db
    from src.transform import transform_data
    from src.release import run_release
    db=synthetic_db.__wrapped__(tmp_path)
    output=tmp_path/'parquet'
    reports=tmp_path/'reports'
    transformed=transform_data(db,reports)
    published=run_release(db_path=db,report_dir=reports,clean_dir=output,publish=True)
    assert published['status']=='PUBLISHED'
    return duckdb, db, output, reports, transformed


def test_phase8_evaluates_verified_synthetic_release_and_is_reproducible(tmp_path):
    duckdb,db,folder,reports,_=synthetic_release(tmp_path)
    from src.evaluate import evaluate_project
    opts=dict(db_path=db,parquet_dir=folder,report_dir=reports,
              dictionary_dir=tmp_path/'dictionary',figures_dir=tmp_path/'figures',
              create_figures=False,deep_compare=True)
    first=evaluate_project(**opts)
    assert first['publication_status']=='PUBLISHED'
    assert first['verification_status']=='VERIFIED'
    assert first['parquet_files']==9
    assert first['deep_comparison_performed']
    assert len(first['rules'])==15
    assert len(first['dimensions'])==6
    assert first['quality_checks']['critical_fails']==0
    assert (reports/'phase8_final_report.md').is_file()
    assert (reports/'phase8_quality_metrics.csv').is_file()
    assert (tmp_path/'dictionary'/'phase8_data_dictionary.csv').is_file()
    assert not any((tmp_path/'figures').glob('*')) if (tmp_path/'figures').exists() else True
    assert first['tables'] == evaluate_project(**opts)['tables']
    assert first['dimensions'] == evaluate_project(**opts)['dimensions']
    assert first['temporal_policy'] == evaluate_project(**opts)['temporal_policy']


def test_phase8_rejects_a_stale_release_when_clean_is_rebuilt(tmp_path):
    duckdb,db,folder,reports,_=synthetic_release(tmp_path)
    from src.transform import transform_data
    from src.evaluate import evaluate_project
    transform_data(db,reports)  # genera nuevo transform_run_id
    with pytest.raises(ValueError,match='obsoleto'):
        evaluate_project(db_path=db,parquet_dir=folder,report_dir=reports,
                         dictionary_dir=tmp_path/'dictionary',
                         figures_dir=tmp_path/'figures',create_figures=False)


def test_phase8_detects_content_mutation_even_when_counts_match(tmp_path):
    duckdb,db,folder,reports,_=synthetic_release(tmp_path)
    from src.evaluate import evaluate_project
    with duckdb.connect(str(db)) as con:
        con.execute("UPDATE clean.orders SET order_status='alterado'")
    with pytest.raises(ValueError,match='difiere de CLEAN'):
        evaluate_project(db_path=db,parquet_dir=folder,report_dir=reports,
                         dictionary_dir=tmp_path/'dictionary',
                         figures_dir=tmp_path/'figures',create_figures=False,
                         deep_compare=True)
