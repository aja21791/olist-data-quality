"""Fase 7: controles de publicación, autorización y rollback.

Las pruebas de exportación real a Parquet se ejecutan cuando DuckDB está
instalado en el entorno Windows (como en el proyecto Olist).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.release import (
    ReleaseBlocked, _swap_release, publish_validated, review_sha256,
    validate_authorization,
)
from src.transform_schema import SCHEMA
from src.pipeline import decide_release_gate


def fake_review(*, accepted=False):
    item = {
        'order_id': 'o1', 'tipo_anomalia': 'TRANSPORTISTA_ANTES_COMPRA',
        'check_id': 'V09.TIME.carrier_before_purchase',
        'source_sha256': 'fingerprint123',
        'decision': 'APROBADA' if accepted else 'PENDIENTE',
        'reviewed_by': 'Experto en calidad' if accepted else '',
        'reviewed_at': '2026-11-13T12:00:00-06:00' if accepted else '',
        'resolution': 'EXCEPCION_ACEPTADA' if accepted else '',
        'evidence_source': 'ticket-123' if accepted else '',
        'review_comments': 'Evidencia consultada' if accepted else '',
    }
    return {
        'details': [item], 'accepted': int(accepted), 'pending': int(not accepted),
        'rejected': 0, 'invalid': 0, 'stale_register_rows': 0,
        'accepted_by_check': {'V09.TIME.carrier_before_purchase': 1} if accepted else {},
    }


def fake_validation(*, temporal_failure=True):
    controls = [
        dict(check_id='V09.TIME.carrier_before_purchase',
             severity='CRITICAL', status='FAIL' if temporal_failure else 'PASS',
             violations=int(temporal_failure)),
        dict(check_id='V09.TIME.delivery_before_carrier',
             severity='CRITICAL', status='PASS', violations=0),
    ]
    return {
        'checks': controls, 'critical_failures': int(temporal_failure),
        'warning_checks': 0, 'status': 'BLOCKED' if temporal_failure else 'PASS',
        'validation_run_id': 'test_v1', 'transform_run_id': 'test_t1',
    }


def good_authorization(review):
    return dict(decision='AUTHORIZE_DOCUMENTED_EXCEPTIONS',
                review_sha256=review_sha256(review),
                business_owner='Responsable negocio', quality_owner='Responsable calidad',
                authorization_reference='acta-1',
                approved_at='2026-11-13T12:00:00-06:00')


def test_blocked_never_creates_parquet_or_removes_previous(tmp_path):
    db = tmp_path / 'db.duckdb'
    db.touch()
    clean = tmp_path / 'data' / 'clean'
    clean.mkdir(parents=True)
    (clean / '.gitkeep').touch()
    previous = (clean / '.gitkeep').read_bytes()
    result = publish_validated(db_path=db, report_dir=tmp_path / 'reports',
                               clean_dir=clean, validation=fake_validation(),
                               review=fake_review(), publish=True)
    assert result['status'] == 'BLOCKED' and result['published'] is False
    assert (clean / '.gitkeep').read_bytes() == previous
    assert not list(tmp_path.rglob('*.parquet'))
    assert (tmp_path / 'reports' / 'phase7_preflight_summary.json').exists()
    assert not (tmp_path / 'reports' / 'phase7_quality_report.md').exists()


def test_digest_has_stable_order_but_changes_with_review():
    review = fake_review()
    extra = {**review['details'][0], 'order_id': 'o2'}
    permuted = {'details': [extra, review['details'][0]]}
    assert review_sha256(permuted) == review_sha256({'details': list(reversed(permuted['details']))})
    changed = {'details': [{**x, 'evidence_source': 'actualizado'} for x in permuted['details']]}
    assert review_sha256(changed) != review_sha256(permuted)


def test_authorization_requires_distinct_roles_and_fresh_digest(tmp_path):
    review = fake_review(accepted=True)
    gate = decide_release_gate(fake_validation(), review)
    assert gate['status'] == 'READY_FOR_PHASE7_WITH_EXCEPTIONS'
    path = tmp_path / 'authorization.json'
    doc = good_authorization(review)
    path.write_text(json.dumps(doc), encoding='utf-8')
    assert validate_authorization(path, review=review, gate=gate) == doc
    doc['quality_owner'] = doc['business_owner']
    path.write_text(json.dumps(doc), encoding='utf-8')
    with pytest.raises(ReleaseBlocked, match='distintos'):
        validate_authorization(path, review=review, gate=gate)
    doc = good_authorization(review)
    doc['review_sha256'] = 'huella-desactualizada'
    path.write_text(json.dumps(doc), encoding='utf-8')
    with pytest.raises(ReleaseBlocked, match='obsoleta'):
        validate_authorization(path, review=review, gate=gate)


def test_accepted_event_still_requires_separate_authorization(tmp_path):
    db = tmp_path / 'db.duckdb'
    db.touch()
    result = publish_validated(db_path=db, report_dir=tmp_path / 'reports',
                               clean_dir=tmp_path / 'data' / 'clean',
                               validation=fake_validation(),
                               review=fake_review(accepted=True), publish=True)
    assert result['status'] == 'BLOCKED'
    assert 'autorización' in result['reasons'][0]
    template = json.loads((tmp_path / 'reports' / 'phase7_authorization_template.json').read_text())
    assert template['approved_at'] == ''  # Nunca autocompletar aprobación


def test_swap_failure_restores_previous_release(tmp_path):
    data = tmp_path / 'data'
    clean, staging = data / 'clean', data / '.incoming'
    clean.mkdir(parents=True)
    staging.mkdir()
    (clean / 'release_manifest.json').write_text('original', encoding='utf-8')
    (staging / 'release_manifest.json').write_text('nuevo', encoding='utf-8')
    (staging / 'quality_report.md').write_text('nuevo reporte', encoding='utf-8')
    report = tmp_path / 'reports' / 'phase7_quality_report.md'
    report.parent.mkdir()
    report.write_text('reporte original', encoding='utf-8')

    def fail_after_backup():
        raise OSError('fallo inyectado')

    with pytest.raises(OSError, match='inyectado'):
        _swap_release(staging, clean, staging / 'quality_report.md', report,
                      inject_failure=fail_after_backup)
    assert (clean / 'release_manifest.json').read_text(encoding='utf-8') == 'original'
    assert report.read_text(encoding='utf-8') == 'reporte original'
    assert staging.exists()


def test_release_publication_checks_real_parquet_and_preserves_schema(tmp_path):
    duckdb = pytest.importorskip('duckdb')
    db = tmp_path / 'test.duckdb'
    connection = duckdb.connect(str(db))
    for schema in ('raw', 'clean', 'audit'):
        connection.execute(f'CREATE SCHEMA {schema}')
    for table, columns in SCHEMA.items():
        fields = ','.join(f'"{column}" VARCHAR' for column in columns)
        connection.execute(f'CREATE TABLE raw."{table}" ({fields})')
        connection.execute(f'CREATE TABLE clean."{table}" ({fields})')
    connection.execute('CREATE TABLE audit.transform_runs (run_id VARCHAR, status VARCHAR, finished_at TIMESTAMP)')
    connection.execute("INSERT INTO audit.transform_runs VALUES ('test_t1','COMPLETED',CURRENT_TIMESTAMP)")
    connection.execute('CREATE TABLE audit.quarantine_records (run_id VARCHAR, source_table VARCHAR)')
    connection.execute('''CREATE TABLE audit.rule_results (
        run_id VARCHAR, rule_id VARCHAR, detected BIGINT, corrected BIGINT,
        quarantined BIGINT, unresolved BIGINT
    )''')
    for i in range(1, 16):
        connection.execute('INSERT INTO audit.rule_results VALUES (?,?,?,?,?,?)',
                           ['test_t1', f'R{i:02d}', 0, 0, 0, 0])
    connection.close()
    val = fake_validation(temporal_failure=False)
    for table in SCHEMA:
        val['checks'].append(dict(check_id=f'V03.BALANCE.{table}',
                                  status='PASS', severity='CRITICAL', population=0,
                                  violations=0))
    val['critical_failures'] = 0
    review = {'details': [], 'accepted': 0, 'pending': 0, 'invalid': 0,
              'rejected': 0, 'stale_register_rows': 0, 'accepted_by_check': {}}
    clean_dir = tmp_path / 'data' / 'clean'
    clean_dir.mkdir(parents=True)
    (clean_dir / '.gitkeep').touch()
    preview = publish_validated(db_path=db, report_dir=tmp_path / 'reports',
                                clean_dir=clean_dir, validation=val, review=review,
                                publish=False)
    assert preview['status'] == 'ELIGIBLE'
    assert not list(tmp_path.rglob('*.parquet'))
    published = publish_validated(db_path=db, report_dir=tmp_path / 'reports',
                                  clean_dir=clean_dir, validation=val, review=review,
                                  publish=True)
    assert published['status'] == 'PUBLISHED'
    files = list(clean_dir.glob('*.parquet'))
    assert len(files) == len(SCHEMA) == 9
    assert (clean_dir / 'release_manifest.json').exists()
    manifest = json.loads((clean_dir / 'release_manifest.json').read_text(encoding='utf-8'))
    assert all(r['parquet_rows'] == 0 for r in manifest['tables'])
    assert (tmp_path / 'reports' / 'phase7_quality_report.md').exists()
    assert not list((tmp_path / 'data').glob('.clean_incoming_*'))
