"""Regresiones R05: no inventar fechas; trazabilidad y gate de publicación."""
from pathlib import Path
import json

import pytest

from src.temporal_policy import POLICY_ID, get_temporal_policy_state
from src.exception_review import review_events, snapshot_fingerprint
from src.pipeline import decide_release_gate


def test_temporal_policy_is_versioned_and_does_not_write_raw():
    source = Path(__file__).resolve().parents[1] / 'src' / 'temporal_policy.py'
    text = source.read_text(encoding='utf-8').upper()
    assert 'R05_CARRIER_TIMESTAMP_UNVERIFIABLE_V1' in POLICY_ID
    assert 'UPDATE CLEAN.ORDERS SET ORDER_DELIVERED_CARRIER_DATE=NULL' in text
    assert 'UPDATE RAW.' not in text
    assert 'AUDIT.TEMPORAL_POLICY_EVENTS' in text


def test_release_contract_exports_sidecar_separate_from_nine_parquet():
    from src.release import TEMPORAL_SIDECAR, TABLES
    assert len(TABLES) == 9
    assert TEMPORAL_SIDECAR.endswith('.csv')
    assert TEMPORAL_SIDECAR not in {f'{t}.parquet' for t in TABLES}


def test_masked_cases_are_not_formally_approved_by_default():
    fake = {'checks': [
        dict(check_id='V09.TIME.carrier_before_purchase', severity='CRITICAL',
             status='PASS', violations=0),
        dict(check_id='V09.TIME.delivery_before_carrier', severity='CRITICAL',
             status='PASS', violations=0)],
        'status':'PASS_WITH_WARNINGS','critical_failures':0,'warning_checks':1}
    review = {'details': [],'accepted':0,'pending':0,'invalid':0,'rejected':0,
              'stale_register_rows':0,'accepted_by_check':{},
              'policy_treated_events':189,'policy_source_investigation_pending':189,
              'policy_invalid_events':0,'policy_missing_register':0}
    gate=decide_release_gate(fake,review)
    assert gate['status']=='READY_FOR_PHASE7'
    assert gate['policy_treated_events']==189
    assert gate['formally_accepted_temporal_events']==0
    assert gate['phase5_raw_critical_failed_checks']==0
    altered={**review,'policy_missing_register':1}
    assert decide_release_gate(fake,altered)['status']=='BLOCKED'


def _changed_raw_dataset(tmp_path):
    duckdb=pytest.importorskip('duckdb',reason='Ejecutar con DuckDB en olist-quality')
    from test_phase4 import synthetic_db as original_fixture
    db=original_fixture.__wrapped__(tmp_path)
    with duckdb.connect(str(db)) as con:
        # El fixture posee dos filas RAW idénticas de o1: ambas reciben el evento.
        con.execute("""UPDATE raw.orders
             SET order_delivered_carrier_date='2017-01-01 09:30:00'
             WHERE order_id='o1'""")
    return db


def test_temporal_masking_preserves_raw_and_prevents_unlogged_changes(tmp_path):
    duckdb=pytest.importorskip('duckdb',reason='Ejecutar con DuckDB en olist-quality')
    from src.transform import transform_data
    from src.validate import validate_clean
    from src.temporal_policy import load_treated_events
    db=_changed_raw_dataset(tmp_path)
    before='2017-01-01 09:30:00'
    transformed=transform_data(db,tmp_path/'reports')
    assert transformed['temporal_policy']['events']==1
    assert transformed['temporal_policy']['orders_masked']==1
    with duckdb.connect(str(db),read_only=True) as con:
        assert con.execute('SELECT order_delivered_carrier_date FROM clean.orders').fetchone()[0] is None
        status=con.execute('''SELECT carrier_timestamp_suppressed_by_quality,
                            carrier_timestamp_quality_status
                            FROM clean.orders_temporal_quality''').fetchone()
        assert status==(True,'QUALITY_SUPPRESSED')
        assert con.execute('SELECT order_delivered_carrier_date FROM raw.orders LIMIT 1').fetchone()[0]==before
        assert con.execute('''SELECT COUNT(*) FROM audit.temporal_policy_events
               WHERE transform_run_id=?''',[transformed['run_id']]).fetchone()[0]==1
        assert con.execute("SELECT COUNT(*) FROM audit.field_changes WHERE rule_id='R05'").fetchone()[0]==1
        assert get_temporal_policy_state(con,transformed['run_id'])['violations']==0
    assert len(load_treated_events(db))==1
    report=validate_clean(db,tmp_path/'reports')
    assert report['critical_failures']==0
    flagged=next(r for r in report['checks'] if r['check_id']=='V09.TEMPORAL_SOURCE_FLAGGED')
    assert flagged['status']=='WARNING' and flagged['violations']==1
    with duckdb.connect(str(db)) as con:
        con.execute("UPDATE clean.orders SET order_delivered_carrier_date='2017-01-03 10:00:00'")
    tampered=validate_clean(db,tmp_path/'reports')
    check=next(r for r in tampered['checks'] if r['check_id']=='V09.TEMPORAL_POLICY_AUDIT')
    assert check['status']=='FAIL'
    assert tampered['status']=='BLOCKED'


def test_full_pipeline_policy_publishes_verifiable_parquet_and_provenance(tmp_path):
    duckdb=pytest.importorskip('duckdb',reason='Ejecutar con DuckDB en olist-quality')
    from src.transform import transform_data
    from src.pipeline import run_pipeline
    from src.release import run_release
    db=_changed_raw_dataset(tmp_path)
    reports=tmp_path/'reports'
    transform_data(db,reports)
    assessed=run_pipeline(db_path=db,report_dir=reports,validate_only=True)
    assert assessed['status']=='READY_FOR_PHASE7'
    assert assessed['gate']['policy_treated_events']==1
    assert assessed['review']['policy_source_investigation_pending']==1
    result=run_release(db_path=db, report_dir=reports,
                       clean_dir=tmp_path/'data'/'clean',publish=True)
    assert result['status']=='PUBLISHED'
    folder=tmp_path/'data'/'clean'
    assert len(list(folder.glob('*.parquet')))==9
    with duckdb.connect() as con:
        data=con.execute("SELECT * FROM read_parquet(?)",[str(folder/'orders.parquet')]).fetchall()
        assert data and data[0][5] is None
    from src.verify_release import verify_release
    check=verify_release(folder,reports)
    assert check['status']=='VERIFIED' and check['parquet_tables']==9
    assert check['temporal_policy_events']==1
    manifest=json.loads((folder/'release_manifest.json').read_text(encoding='utf-8'))
    assert manifest['temporal_policy']['events']==1
    assert (folder/'temporal_quality_exceptions.csv').exists()


def test_verify_release_refuses_missing_manifest(tmp_path):
    from src.verify_release import verify_release
    with pytest.raises((FileNotFoundError, ImportError)):
        verify_release(tmp_path, tmp_path / 'reports')
