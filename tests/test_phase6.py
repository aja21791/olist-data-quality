"""Pruebas del registro de excepciones y de la orquestación de Fase 6.

Los casos unitarios no necesitan DuckDB; el entorno del usuario debe ejecutar
la suite de integración de fases anteriores con DuckDB instalado.
"""
from datetime import datetime, timezone
from pathlib import Path
import pytest

from src.exception_review import (
    EVENT_TYPES, make_event_row, review_events, snapshot_fingerprint, validate_review,
)
from src.pipeline import decide_release_gate, run_pipeline


def event(code='TRANSPORTISTA_ANTES_COMPRA', order_id='order-1'):
    return make_event_row({
        'order_id': order_id, 'order_status': 'delivered',
        'order_purchase_timestamp': '2018-06-11 12:24:48',
        'order_approved_at': '2018-06-11 13:32:18',
        'order_delivered_carrier_date': '2018-06-11 12:05:00',
        'order_delivered_customer_date': '2018-06-18 16:51:52',
        'tipo_anomalia': code, 'desfase_minutos_exacto': -19.8,
    })


def accepted(item):
    return {**item, 'estado_revision': 'APROBADA',
            'resolucion': 'EXCEPCION_ACEPTADA',
            'revisado_por': 'Responsable de calidad',
            'fecha_revision': '2026-11-13T12:00:00-06:00',
            'evidencia_fuente': 'ticket-interno-123',
            'comentarios_revision': 'Aceptación documentada; conservar fecha original.'}


def fake_validation(violations=1, *, add_other=False):
    checks = [{'check_id': 'V09.TIME.carrier_before_purchase',
               'severity': 'CRITICAL', 'status': 'FAIL', 'violations': violations}]
    checks.append({'check_id': 'V09.TIME.delivery_before_carrier',
                   'severity': 'CRITICAL', 'status': 'PASS', 'violations': 0})
    if add_other:
        checks.append({'check_id': 'V12.FK.orders.customer_id',
                       'severity': 'CRITICAL', 'status': 'FAIL', 'violations': 1})
    return {'checks': checks, 'critical_failures': sum(r['status']=='FAIL' for r in checks),
            'warning_checks': 0, 'status': 'BLOCKED', 'validation_run_id': 'vid'}


def test_pending_register_blocks_automatic_approval():
    row = event()
    result = review_events([row], [row])
    assert result['pending'] == 1 and result['accepted'] == 0
    gate = decide_release_gate(fake_validation(), result)
    assert gate['status'] == 'BLOCKED'
    assert gate['unresolved_critical_events'] == 1


def test_acceptance_requires_real_review_fields():
    row = event()
    incomplete = {**row, 'estado_revision': 'APROBADA'}
    decision, reason = validate_review(row, incomplete)
    assert decision == 'INVALIDA' and 'resolucion' in reason
    approved = accepted(row)
    approved['fecha_revision'] = '2026-11-13'  # sin zona horaria
    assert validate_review(row, approved)[0] == 'INVALIDA'


def test_documented_acceptance_does_not_erase_raw_incidence():
    row = event()
    current = review_events([row], [accepted(row)],
                            at=datetime(2026, 11, 14, tzinfo=timezone.utc))
    gate = decide_release_gate(fake_validation(), current)
    assert current['accepted'] == 1 and current['pending'] == 0
    assert gate['phase5_raw_critical_failed_checks'] == 1
    assert gate['formally_accepted_temporal_events'] == 1
    assert gate['status'] == 'READY_FOR_PHASE7_WITH_EXCEPTIONS'
    assert gate['phase5_original_status'] == 'BLOCKED'


def test_other_critical_checks_cannot_be_overridden():
    row = event()
    assessed = review_events([row], [accepted(row)],
                             at=datetime(2026, 11, 14, tzinfo=timezone.utc))
    decision = decide_release_gate(fake_validation(add_other=True), assessed)
    assert decision['status'] == 'BLOCKED'
    assert 'V12.FK.orders.customer_id' in decision['unexempted_critical_checks']


def test_change_in_timestamp_invalidates_approval():
    row = event()
    approved = accepted(row)
    changed = {**row, 'order_delivered_carrier_date': '2018-06-11 12:04:00'}
    assert snapshot_fingerprint(row) != snapshot_fingerprint(changed)
    assert validate_review(changed, approved)[0] == 'INVALIDA'


def test_unregistered_event_remains_pending_and_duplicates_refused():
    r1 = event(order_id='one')
    r2 = event(order_id='two')
    result = review_events([r1, r2], [r1])
    assert result['total_events'] == 2 and result['pending'] == 2
    with pytest.raises(ValueError, match='duplicadas'):
        review_events([r1], [r1, r1])


def test_stale_approval_blocks_publication_candidate():
    r1 = event(order_id='one')
    obsolete = accepted(event(order_id='two'))
    assessed = review_events([r1], [r1, obsolete])
    gate = decide_release_gate(fake_validation(), assessed)
    assert assessed['stale_register_rows'] == 1
    assert gate['status'] == 'BLOCKED'


def test_temporal_sql_reads_clean_without_modifying_source():
    from src.exception_review import SQL_PATH
    sql = SQL_PATH.read_text(encoding='utf-8').upper()
    assert 'FROM CLEAN.ORDERS' in sql
    assert 'UNION ALL' in sql
    assert 'UPDATE ' not in sql
    assert 'DELETE ' not in sql
    assert 'CREATE ' not in sql
    assert set(EVENT_TYPES) == {'TRANSPORTISTA_ANTES_COMPRA', 'ENTREGA_ANTES_TRANSPORTISTA'}


def test_full_pipeline_stage_order_without_database(monkeypatch, tmp_path):
    """Inyectar etapas sin IO para verificar el orden y la propagación del bloqueo."""
    import src.pipeline as module
    events = []
    validation = fake_validation()
    assessment = review_events([event()], [event()])
    monkeypatch.setattr(module, 'create_pipeline_audit', lambda db: None)
    monkeypatch.setattr(module, '_save_run', lambda *a, **kw: events.append(('audit', kw.get('status'))))
    monkeypatch.setattr(module, 'load_raw', lambda **kw: events.append(('extract', None)))
    monkeypatch.setattr(module, 'diagnose_raw', lambda **kw: events.append(('diagnose', None)))
    monkeypatch.setattr(module, 'transform_data', lambda **kw: events.append(('transform', None)))
    monkeypatch.setattr(module, 'validate_clean', lambda **kw: (events.append(('validate', None)), validation)[1])
    monkeypatch.setattr(module, 'assess_temporal_exceptions', lambda **kw: (events.append(('review', None)), assessment)[1])
    monkeypatch.setattr(module, '_save_step', lambda *a, **kw: None)
    result = run_pipeline(db_path=tmp_path / 'dummy.duckdb',
                          report_dir=tmp_path / 'reports', figures=False)
    assert [x[0] for x in events if x[0] != 'audit'] == [
        'extract', 'diagnose', 'transform', 'validate', 'review'
    ]
    assert result['status'] == 'BLOCKED'
    assert (tmp_path / 'reports' / 'phase6_pipeline_report.md').is_file()
    assert not list(tmp_path.rglob('*.parquet'))


def test_validate_only_skips_all_upstream_stages(monkeypatch, tmp_path):
    import src.pipeline as module
    called = []
    validation = fake_validation()
    reviewed = review_events([event()], [event()])
    monkeypatch.setattr(module, 'create_pipeline_audit', lambda db: None)
    monkeypatch.setattr(module, '_save_run', lambda *a, **kw: None)
    monkeypatch.setattr(module, '_save_step', lambda *a, **kw: None)
    monkeypatch.setattr(module, 'load_raw', lambda **kw: called.append('extract'))
    monkeypatch.setattr(module, 'diagnose_raw', lambda **kw: called.append('diagnose'))
    monkeypatch.setattr(module, 'transform_data', lambda **kw: called.append('transform'))
    monkeypatch.setattr(module, 'validate_clean', lambda **kw: (called.append('validate'), validation)[1])
    monkeypatch.setattr(module, 'assess_temporal_exceptions',
                        lambda **kw: (called.append('review'), reviewed)[1])
    result = run_pipeline(db_path=tmp_path / 'dummy.duckdb',
                          report_dir=tmp_path, validate_only=True)
    assert called == ['validate', 'review']
    assert result['status'] == 'BLOCKED'


def test_phase6_integration_keeps_blocked_until_valid_review(tmp_path):
    """Integración local: requiere DuckDB y las mismas tablas sintéticas de fase 4."""
    duckdb = pytest.importorskip('duckdb', reason='Ejecutar en entorno olist-quality')
    from test_phase4 import synthetic_db as fixture_fn
    from src.transform import transform_data
    from src.pipeline import run_pipeline
    import csv
    db = fixture_fn.__wrapped__(tmp_path)
    report_dir = tmp_path / 'quality_reports'
    transform_data(db, report_dir)
    with duckdb.connect(str(db)) as con:
        con.execute("""UPDATE clean.orders
            SET order_delivered_carrier_date = '2016-12-31 10:00:00'
            WHERE order_id = 'o1'""")
    one = run_pipeline(db_path=db, report_dir=report_dir, validate_only=True)
    assert one['status'] == 'BLOCKED'
    assert one['review']['total_events'] == 1
    assert one['review']['pending'] == 1
    register = report_dir / 'phase5_temporal_review_register.csv'
    assert register.is_file()
    with register.open(encoding='utf-8-sig', newline='') as file:
        review_rows = list(csv.DictReader(file))
    review_rows[0].update(estado_revision='APROBADA', resolucion='EXCEPCION_ACEPTADA',
                          evidencia_fuente='ticket-de-prueba', revisado_por='Tester',
                          fecha_revision='2020-01-01T12:00:00-06:00',
                          comentarios_revision='Aprobación sintética para el caso de prueba')
    with register.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=review_rows[0])
        writer.writeheader()
        writer.writerows(review_rows)
    two = run_pipeline(db_path=db, report_dir=report_dir, validate_only=True)
    assert two['status'] == 'READY_FOR_PHASE7_WITH_EXCEPTIONS'
    assert two['validation']['status'] == 'BLOCKED'
    with duckdb.connect(str(db), read_only=True) as con:
        assert con.execute('SELECT COUNT(*) FROM audit.pipeline_runs').fetchone()[0] == 2
        assert con.execute('SELECT COUNT(*) FROM clean.orders').fetchone()[0] == 1
        assert con.execute('SELECT COUNT(*) FROM raw.orders').fetchone()[0] == 2
    assert not list(tmp_path.rglob('*.parquet'))
