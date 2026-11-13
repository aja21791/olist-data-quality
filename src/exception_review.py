"""Fase 6: conciliación explícita de decisiones sobre anomalías temporales.

No corrige fechas, no modifica RAW/CLEAN y nunca aprueba excepciones por defecto.
La aprobación documental sólo es válida si el registro de revisión coincide con
el evento actualmente detectado y posee responsable, fecha, decisión y evidencia.
La evidencia declarada no se comprueba externamente: requiere revisión humana.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config import DB_PATH, QUALITY_REPORTS_DIR, ROOT_DIR

SQL_PATH = ROOT_DIR / 'sql' / 'validations' / '08_temporal_exceptions.sql'
REGISTER_NAME = 'phase5_temporal_review_register.csv'
EVENT_TYPES = {
    'TRANSPORTISTA_ANTES_COMPRA': 'V09.TIME.carrier_before_purchase',
    'ENTREGA_ANTES_TRANSPORTISTA': 'V09.TIME.delivery_before_carrier',
}
SNAPSHOT_FIELDS = (
    'order_id', 'order_status', 'order_purchase_timestamp', 'order_approved_at',
    'order_delivered_carrier_date', 'order_delivered_customer_date',
)
REGISTER_FIELDS = (
    *SNAPSHOT_FIELDS, 'tipo_anomalia', 'desfase_minutos_exacto',
    'prioridad_revision', 'estado_revision', 'resolucion', 'evidencia_fuente',
    'revisado_por', 'fecha_revision', 'comentarios_revision',
)
ALLOWED_STATUSES = {'PENDIENTE', 'APROBADA', 'RECHAZADA'}


def text_timestamp(value) -> str:
    if value is None or value == '':
        return ''
    if not isinstance(value, datetime):
        value = str(value).strip().replace('T', ' ')
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    return value.isoformat(sep=' ', timespec='microseconds').rstrip('0').rstrip('.')


def snapshot_fingerprint(record: dict) -> str:
    """La huella impide aplicar aprobaciones a fechas/estados que hayan cambiado."""
    snapshot = {key: text_timestamp(record.get(key)) for key in SNAPSHOT_FIELDS}
    snapshot['tipo_anomalia'] = record.get('tipo_anomalia', '')
    canonical = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def make_event_row(row: dict) -> dict:
    item = {key: text_timestamp(row.get(key)) for key in SNAPSHOT_FIELDS}
    item['tipo_anomalia'] = str(row['tipo_anomalia'])
    item['desfase_minutos_exacto'] = str(row['desfase_minutos_exacto'])
    item['prioridad_revision'] = ('ALTA' if abs(float(row['desfase_minutos_exacto'])) > 1440
                                 else 'MEDIA')
    item.update(estado_revision='PENDIENTE', resolucion='', evidencia_fuente='',
                revisado_por='', fecha_revision='', comentarios_revision='')
    return item


def _read_register(path: Path) -> list[dict]:
    with path.open('r', encoding='utf-8-sig', newline='') as file:
        reader = csv.DictReader(file)
        missing = set(REGISTER_FIELDS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f'Registro de revisión sin columnas obligatorias: {sorted(missing)}')
        return list(reader)


def _write_csv(path: Path, rows: list[dict], fields: tuple[str, ...]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def validate_review(event: dict, reviewed: dict | None, *, at=None) -> tuple[str, str]:
    """Devuelve (decisión efectiva, motivo). Nunca interpreta PENDIENTE como aprobación."""
    if reviewed is None:
        return 'PENDIENTE', 'Evento no incluido en el registro de revisión'
    if snapshot_fingerprint(event) != snapshot_fingerprint(reviewed):
        return 'INVALIDA', 'Cambio en fecha/estado o tipo; revisión desactualizada'
    status = (reviewed.get('estado_revision') or 'PENDIENTE').strip().upper()
    if status not in ALLOWED_STATUSES:
        return 'INVALIDA', f'Estado de revisión no reconocido: {status}'
    if status != 'APROBADA':
        return status, 'Pendiente de evidencia y decisión' if status == 'PENDIENTE' else 'Rechazada por el responsable'
    if (reviewed.get('resolucion') or '').strip().upper() != 'EXCEPCION_ACEPTADA':
        return 'INVALIDA', 'APROBADA requiere resolucion=EXCEPCION_ACEPTADA'
    for field in ('evidencia_fuente', 'revisado_por', 'fecha_revision', 'comentarios_revision'):
        if not (reviewed.get(field) or '').strip():
            return 'INVALIDA', f'APROBADA requiere {field} no vacío'
    try:
        value = datetime.fromisoformat(reviewed['fecha_revision'].strip())
        if value.tzinfo is None or value.utcoffset() is None:
            return 'INVALIDA', 'fecha_revision debe contener zona horaria, ej. -06:00'
        if value.astimezone(timezone.utc) > (at or datetime.now(timezone.utc)):
            return 'INVALIDA', 'La fecha de revisión no puede estar en el futuro'
    except ValueError:
        return 'INVALIDA', 'fecha_revision ISO 8601 inválida'
    return 'APROBADA', 'Excepción aceptada documentalmente; fechas originales conservadas'


def review_events(current: list[dict], registered: list[dict], *, at=None) -> dict:
    """Conciliar por clave compuesta, dejando visibles los eventos pendientes y obsoletos."""
    keys = [(r['order_id'], r['tipo_anomalia']) for r in registered]
    duplicates = [k for k, qty in Counter(keys).items() if qty > 1]
    if duplicates:
        raise ValueError(f'El registro contiene claves duplicadas: {duplicates[:5]}')
    indexed = dict(zip(keys, registered))
    now_keys = set()
    details, accepted_by_check = [], Counter()
    for event in current:
        key = (event['order_id'], event['tipo_anomalia'])
        if key in now_keys:
            raise ValueError(f'Evento duplicado desde CLEAN: {key}')
        now_keys.add(key)
        if event['tipo_anomalia'] not in EVENT_TYPES:
            raise ValueError(f'Anomalía no soportada: {event["tipo_anomalia"]}')
        decision, note = validate_review(event, indexed.get(key), at=at)
        check = EVENT_TYPES[event['tipo_anomalia']]
        if decision == 'APROBADA':
            accepted_by_check[check] += 1
        reviewed = indexed.get(key) or {}
        details.append(dict(order_id=event['order_id'], tipo_anomalia=event['tipo_anomalia'],
                            check_id=check, prioridad_revision=event['prioridad_revision'],
                            decision=decision, note=note,
                            reviewed_by=reviewed.get('revisado_por', ''),
                            reviewed_at=reviewed.get('fecha_revision', ''),
                            resolution=reviewed.get('resolucion', ''),
                            evidence_source=reviewed.get('evidencia_fuente', ''),
                            review_comments=reviewed.get('comentarios_revision', ''),
                            source_sha256=snapshot_fingerprint(event)))
    stale = sorted(set(indexed) - now_keys)
    effective = Counter(r['decision'] for r in details)
    return dict(total_events=len(details), accepted=int(effective['APROBADA']),
                pending=int(effective['PENDIENTE']), rejected=int(effective['RECHAZADA']),
                invalid=int(effective['INVALIDA']), stale_register_rows=len(stale),
                accepted_by_check=dict(accepted_by_check), details=details,
                stale_keys=[{'order_id': k[0], 'tipo_anomalia': k[1]} for k in stale])


def load_temporal_events(db_path=DB_PATH) -> list[dict]:
    import duckdb
    with duckdb.connect(str(db_path), read_only=True) as con:
        record = con.execute(SQL_PATH.read_text(encoding='utf-8'))
        fields = [d[0] for d in record.description]
        return [make_event_row(dict(zip(fields, row))) for row in record.fetchall()]


def _audit_review(db_path: Path, outcome: dict, pipeline_run_id: str):
    import duckdb
    with duckdb.connect(str(db_path)) as con:
        con.execute('CREATE SCHEMA IF NOT EXISTS audit')
        con.execute('''CREATE TABLE IF NOT EXISTS audit.temporal_review_assessments (
            assessment_id VARCHAR, pipeline_run_id VARCHAR, assessed_at TIMESTAMP,
            order_id VARCHAR, issue_code VARCHAR, check_id VARCHAR,
            decision VARCHAR, evidence_fingerprint VARCHAR, note VARCHAR,
            reviewed_by VARCHAR, reviewed_at VARCHAR, resolution VARCHAR,
            evidence_source VARCHAR, review_comments VARCHAR
        )''')
        assessment_id = uuid4().hex
        at = datetime.now(timezone.utc).replace(tzinfo=None)
        payload = [
            (assessment_id, pipeline_run_id, at, r['order_id'], r['tipo_anomalia'],
             r['check_id'], r['decision'], r['source_sha256'], r['note'],
             r['reviewed_by'], r['reviewed_at'], r['resolution'],
             r['evidence_source'], r['review_comments'])
            for r in outcome['details']
        ]
        if payload:
            con.executemany('''INSERT INTO audit.temporal_review_assessments
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', payload)


def assess_temporal_exceptions(db_path=DB_PATH, report_dir=QUALITY_REPORTS_DIR,
                               register_path=None, pipeline_run_id=None) -> dict:
    """Crea registro PENDIENTE sólo si falta; nunca sobrescribe aprobaciones humanas."""
    db_path = Path(db_path)
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    register = Path(register_path) if register_path else report_dir / REGISTER_NAME
    current = load_temporal_events(db_path)
    created = not register.exists()
    if created:
        _write_csv(register, current, REGISTER_FIELDS)
    registered = _read_register(register)
    result = review_events(current, registered)
    result['register_created'] = created
    result['register_path'] = str(register)
    result['evaluated_at_utc'] = datetime.now(timezone.utc).isoformat()
    _audit_review(db_path, result, pipeline_run_id or uuid4().hex)
    _write_csv(report_dir / 'phase6_temporal_decisions.csv', result['details'],
               ('order_id', 'tipo_anomalia', 'check_id', 'prioridad_revision',
                'decision', 'note', 'source_sha256', 'reviewed_by',
                'reviewed_at', 'resolution', 'evidence_source', 'review_comments'))
    exported = {k: v for k, v in result.items() if k != 'details'}
    (report_dir / 'phase6_temporal_review_summary.json').write_text(
        json.dumps(exported, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description='Revisión documentada de excepciones temporales CLEAN')
    parser.add_argument('--db', type=Path, default=DB_PATH)
    parser.add_argument('--reports', type=Path, default=QUALITY_REPORTS_DIR)
    parser.add_argument('--register', type=Path, default=None,
                        help='Archivo CSV de revisión humana, si no se usa el predeterminado')
    args = parser.parse_args()
    out = assess_temporal_exceptions(args.db, args.reports, args.register)
    print(f"[REVISIÓN] eventos={out['total_events']}; aprobados={out['accepted']}; "
          f"pendientes={out['pending']}; rechazados={out['rejected']}; inválidos={out['invalid']}")
    print('[REGISTRO]', out['register_path'])
    if out['pending'] or out['rejected'] or out['invalid']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
