"""Fase 7: publicar únicamente CLEAN aprobado en archivos Parquet verificables.

La carpeta data/clean sólo se reemplaza DESPUÉS de un gate conforme y después
que se han comprobado los nueve Parquet en un directorio temporal. Una sustitución
por renombrado con rollback es más segura que reemplazar nueve archivos por
separado, aunque no es una transacción distribuida de archivos y DuckDB.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config import DATA_CLEAN_DIR, DB_PATH, QUALITY_REPORTS_DIR, ROOT_DIR
from .exception_review import assess_temporal_exceptions
from .pipeline import decide_release_gate
from .transform_schema import SCHEMA
from .validate import validate_clean

LOG = logging.getLogger(__name__)
TABLES = tuple(SCHEMA)
RELEASE_FILE = 'release_manifest.json'
RELEASE_REPORT = 'quality_report.md'
TEMPORAL_SIDECAR = 'temporal_quality_exceptions.csv'
SQL_EXPORTS = ROOT_DIR / 'sql' / 'exports'


class ReleaseBlocked(RuntimeError):
    """La política no autoriza publicar. No equivale a error técnico."""


def utc_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_sha256(path: Path) -> str:
    sha = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            sha.update(block)
    return sha.hexdigest()


def review_sha256(review: dict) -> str:
    """Fija las decisiones al evento y a la evidencia declarada; no valida su autenticidad."""
    fields = ('order_id', 'tipo_anomalia', 'check_id', 'source_sha256', 'decision',
              'reviewed_by', 'reviewed_at', 'resolution', 'evidence_source',
              'review_comments')
    rows = [tuple(str(row.get(k) or '') for k in fields) for row in review['details']]
    encoded = json.dumps(sorted(rows), ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def validate_authorization(path: Path | None, *, review: dict, gate: dict) -> dict | None:
    """Autorización externa exigida si la publicación conserva anomalías críticas.

    El programa verifica estructura y vínculo al digest, NO comprueba identidad o
    existencia del respaldo fuera del proyecto. Es una decisión de negocio humana.
    """
    if gate['status'] != 'READY_FOR_PHASE7_WITH_EXCEPTIONS':
        return None
    if path is None or not Path(path).is_file():
        raise ReleaseBlocked('Hay excepciones aceptadas: falta autorización independiente '
                             'del responsable de datos (--authorization).')
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if data.get('decision') != 'AUTHORIZE_DOCUMENTED_EXCEPTIONS':
        raise ReleaseBlocked('Autorización sin decisión AUTHORIZE_DOCUMENTED_EXCEPTIONS')
    if data.get('review_sha256') != review_sha256(review):
        raise ReleaseBlocked('La huella de revisiones difiere: autorización obsoleta')
    for field in ('business_owner', 'quality_owner', 'authorization_reference', 'approved_at'):
        if not isinstance(data.get(field), str) or not data[field].strip():
            raise ReleaseBlocked(f'Autorización incompleta: {field}')
    if data['business_owner'].strip().casefold() == data['quality_owner'].strip().casefold():
        raise ReleaseBlocked('Se requieren roles de responsable del negocio y de calidad distintos')
    try:
        approved_at = datetime.fromisoformat(data['approved_at'].strip().replace('Z', '+00:00'))
    except ValueError as exc:
        raise ReleaseBlocked('approved_at no es una fecha ISO 8601 válida') from exc
    if approved_at.tzinfo is None or approved_at.utcoffset() is None:
        raise ReleaseBlocked('approved_at debe indicar zona horaria')
    if approved_at.astimezone(timezone.utc) > datetime.now(timezone.utc):
        raise ReleaseBlocked('approved_at no puede estar en el futuro')
    # No guardar nombres y referencias de la autorización en un archivo público
    # salvo donde sea imprescindible: el manifiesto conserva sólo su digest.
    return data


def _authorization_template(review: dict) -> dict:
    return {
        'decision': '',
        'review_sha256': review_sha256(review),
        'business_owner': '',
        'quality_owner': '',
        'authorization_reference': '',
        'approved_at': '',
    }


def _snapshot_counts(con) -> list[dict]:
    """Conteos y columnas obtenidos de la BD, no del inventario supuesto."""
    rows = []
    for table in TABLES:
        raw_count = int(con.execute(f'SELECT COUNT(*) FROM raw."{table}"').fetchone()[0])
        clean_count = int(con.execute(f'SELECT COUNT(*) FROM clean."{table}"').fetchone()[0])
        schema = [(str(r[0]), str(r[1])) for r in
                  con.execute(f'DESCRIBE SELECT * FROM clean."{table}"').fetchall()]
        if [r[0] for r in schema] != SCHEMA[table]:
            raise ReleaseBlocked(f'La estructura CLEAN de {table} no coincide con el catálogo')
        rows.append({'table': table, 'raw_rows': raw_count,
                     'clean_rows': clean_count, 'schema': schema})
    return rows


def _compare_validated_counts(con, validation: dict, counts: list[dict]):
    """Contrastar el lote actual con el run completo que validó Fase 5."""
    latest = con.execute('''SELECT run_id FROM audit.transform_runs
                            WHERE status='COMPLETED'
                            ORDER BY finished_at DESC NULLS LAST, run_id DESC
                            LIMIT 1''').fetchone()
    if not latest or latest[0] != validation.get('transform_run_id'):
        raise ReleaseBlocked('La transformación más reciente no es la validada por Fase 5')
    by_check = {row['check_id']: row for row in validation['checks']}
    for r in counts:
        table = r['table']
        check = by_check.get(f'V03.BALANCE.{table}')
        if not check or check['status'] != 'PASS' or int(check['population']) != r['raw_rows']:
            raise ReleaseBlocked(f'Cambiaron las cantidades de {table} desde la validación')
        quarantined = int(con.execute('''SELECT COUNT(*) FROM audit.quarantine_records
                  WHERE run_id=? AND source_table=?''',
                  [latest[0], table]).fetchone()[0])
        if r['raw_rows'] != r['clean_rows'] + quarantined:
            raise ReleaseBlocked(f'El balance RAW=CLEAN+cuarentena ya no cierra: {table}')


def _extract_rules(con, transform_run_id: str | None) -> list[dict]:
    if not transform_run_id:
        return []
    try:
        rows = con.execute('''SELECT rule_id, SUM(detected), SUM(corrected), SUM(quarantined),
                                   SUM(unresolved)
                            FROM audit.rule_results WHERE run_id=? GROUP BY rule_id ORDER BY rule_id''',
                           [transform_run_id]).fetchall()
    except Exception as exc:
        raise ReleaseBlocked('No se pueden verificar las reglas de auditoría R01–R15') from exc
    observed = {r[0] for r in rows}
    if observed != {f'R{i:02d}' for i in range(1, 16)}:
        raise ReleaseBlocked(f'Catálogo de ejecución incompleto: {sorted(observed)}')
    return [dict(rule_id=r[0], detected=int(r[1]), corrected=int(r[2]),
                 quarantined=int(r[3]), unresolved=int(r[4])) for r in rows]


def _build_report(manifest: dict, rules: list[dict], validation: dict, gate: dict) -> str:
    header = [
        '# Fase 7 — Reporte de calidad y publicación', '',
        f"- Fecha UTC: `{manifest['created_at_utc']}`",
        f"- Release ID: `{manifest['release_id']}`",
        f"- Validación de Fase 5: `{manifest['validation_run_id']}`",
        f"- Transformación de Fase 4: `{manifest['transform_run_id']}`",
        f"- Dictamen final: **{manifest['status']}**",
        f"- Controles críticos fallidos sin excepción: {len(gate['unexempted_critical_checks'])}",
        f"- Eventos temporales sin resolver: {gate['unresolved_critical_events']}",
        f"- Eventos de origen tratados por R05: {gate.get('policy_treated_events',0)}",
        f"- Investigaciones de origen pendientes: {gate.get('policy_source_investigation_pending',0)}",
        f"- Excepciones formalmente aceptadas: {gate['formally_accepted_temporal_events']}",
        f"- Advertencias Fase 5: {validation['warning_checks']}", '',
        '## Tablas verificadas', '',
        '| Tabla | RAW | CLEAN | Parquet | SHA-256 |',
        '|---|---:|---:|---:|---|',
    ]
    for r in manifest['tables']:
        header.append(f"| {r['table']} | {r['raw_rows']} | {r['clean_rows']} | "
                      f"{r['parquet_rows']} | `{r['sha256']}` |")
    header += ['', '## Reglas de calidad R01–R15', '',
               'Son incidencias de controles; las mismas filas pueden aparecer en varias reglas.',
               'Pendientes no equivale automáticamente a errores críticos.', '',
               '| Regla | Detectadas | Corregidas | Cuarentena | Pendientes |',
               '|---|---:|---:|---:|---:|']
    for r in rules:
        header.append(f"| {r['rule_id']} | {r['detected']} | {r['corrected']} | "
                      f"{r['quarantined']} | {r['unresolved']} |")
    if not rules:
        header.append('| Sin datos de ejecución de las 15 reglas | — | — | — | — |')
    header += ['', '## Controles no aprobados', '']
    for c in validation['checks']:
        if c['status'] != 'PASS':
            header.append(f"- `{c['check_id']}`: {c['status']} — {c['violations']} incidencias")
    header += ['', '## Límites y responsabilidad', '',
               '- Los datos de RAW no se modificaron para esta publicación.',
               '- Los outliers IQR se conservan mientras no exista evidencia de error.',
               '- La exactitud no se ha comprobado contra una fuente externa.',
               '- Una excepción documental no prueba que una fecha sea correcta.',
               '- R05 marca NULL únicamente en CLEAN para eventos de transporte no verificables.',
               '- temporal_quality_exceptions.csv preserva identificador, motivo y fecha original.',
               '- Para distinguir NULL de origen de supresión por calidad, consultar el CSV de trazabilidad.',
               '- Los CSV originales y las decisiones deben conservarse para auditoría.',
               '- Antes y después: detected/corrected/unresolved son incidencias por regla, '
               'no porcentajes agregables ni necesariamente filas únicas.', '',
               '## Entregables', '',
               '- Nueve archivos Parquet comprimidos con ZSTD y verificados.',
               '- Manifest con esquemas, filas, tamaño, huellas y referencia a validaciones.',
               '- Este reporte de calidad y el historial de auditoría.', '']
    return '\n'.join(header)


def _atomic_write_json(path: Path, document: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        temp.write_text(json.dumps(document, ensure_ascii=False, indent=2, default=str),
                        encoding='utf-8')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _preflight_report(report_dir: Path, validation: dict, gate: dict, review: dict,
                      *, auth_needed: bool, errors: list[str]):
    report_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {
        'checked_at_utc': utc_text(), 'status': 'BLOCKED' if errors else 'ELIGIBLE',
        'validation_run_id': validation['validation_run_id'],
        'validation_status': validation['status'],
        'release_gate': gate, 'review_sha256': review_sha256(review),
        'authorization_required': auth_needed, 'reasons': errors,
        'published': False,
    }
    _atomic_write_json(report_dir / 'phase7_preflight_summary.json', snapshot)
    lines = ['# Fase 7 — Comprobación previa', '',
             f"Estado: **{snapshot['status']}**", '',
             f"Validación: `{validation['validation_run_id']}`",
             f"Gate de Fase 6: `{gate['status']}`",
             f"Anomalías pendientes: {gate['unresolved_critical_events']}",
             f"Huella de revisión: `{snapshot['review_sha256']}`", '']
    lines += [f"Eventos tratados por R05: {gate.get('policy_treated_events',0)}",
              f"Investigaciones de origen pendientes: {gate.get('policy_source_investigation_pending',0)}", '']
    if errors:
        lines += ['## Motivos de bloqueo', ''] + [f'- {error}' for error in errors]
    else:
        lines += ['La publicación requiere una acción explícita (`--publish`).']
    (report_dir / 'phase7_preflight_report.md').write_text('\n'.join(lines) + '\n',
                                                            encoding='utf-8')
    if auth_needed:
        template_path = report_dir / 'phase7_authorization_template.json'
        if not template_path.exists():
            _atomic_write_json(template_path, _authorization_template(review))
    return snapshot


def _stage_parquet(con, staging: Path, counts: list[dict]) -> list[dict]:
    """COPY y verificación se hacen contra una misma transacción de lectura."""
    records = []
    for r in counts:
        table = r['table']
        dest = staging / f'{table}.parquet'
        # Literal de ruta escapado, no se admiten nombres arbitrarios en TABLES.
        path_sql = "'" + dest.resolve().as_posix().replace("'", "''") + "'"
        sql_copy = (SQL_EXPORTS / '01_clean_to_parquet.sql').read_text(encoding='utf-8')
        sql_count = (SQL_EXPORTS / '02_verify_parquet_count.sql').read_text(encoding='utf-8')
        sql_schema = (SQL_EXPORTS / '03_verify_parquet_schema.sql').read_text(encoding='utf-8')
        con.execute(sql_copy.format(table=f'"{table}"', file=path_sql))
        count = int(con.execute(sql_count.format(file=path_sql)).fetchone()[0])
        descriptor = [(str(x[0]), str(x[1])) for x in
                      con.execute(sql_schema.format(file=path_sql)).fetchall()]
        if count != r['clean_rows'] or descriptor != r['schema']:
            raise ReleaseBlocked(f'Parquet {table}: filas o esquema diferentes a CLEAN')
        records.append({**r, 'file': dest.name, 'parquet_rows': count,
                        'bytes': dest.stat().st_size, 'sha256': file_sha256(dest)})
        LOG.info('PARQUET verificado | %-20s | %s filas', table, count)
    return records


def _stage_temporal_sidecar(con, staging: Path, transform_run_id: str | None,
                           expected_events: int) -> dict:
    """Trazabilidad junto a los 9 Parquet: NO constituye una décima tabla CLEAN."""
    dest = staging / TEMPORAL_SIDECAR
    exists = con.execute("""SELECT 1 FROM information_schema.tables
       WHERE table_schema='audit' AND table_name='temporal_policy_events'""").fetchone()
    if not exists and expected_events:
        raise ReleaseBlocked('No está disponible la auditoría R05 para exportar la trazabilidad')
    rows = []
    if exists:
        rows = con.execute('''SELECT order_id, issue_code,
            CAST(order_purchase_timestamp AS VARCHAR),
            CAST(order_delivered_carrier_date AS VARCHAR),
            CAST(order_delivered_customer_date AS VARCHAR), policy_id
            FROM audit.temporal_policy_events WHERE transform_run_id=?
            ORDER BY order_id, issue_code''', [transform_run_id]).fetchall()
    if len(rows) != expected_events:
        raise ReleaseBlocked('Las incidencias R05 de AUDIT difieren del gate previo')
    with dest.open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.writer(handle)
        writer.writerow(['order_id', 'issue_code', 'original_purchase_timestamp',
                         'original_carrier_timestamp', 'original_delivery_timestamp',
                         'policy_id'])
        writer.writerows(rows)
    return {'file': TEMPORAL_SIDECAR, 'events': len(rows),
            'sha256': file_sha256(dest), 'bytes': dest.stat().st_size}


def _write_history(db_path: Path, release: dict, status: str, error: str = ''):
    """La auditoría es adicional a la publicación; nunca incluye datos de usuarios."""
    import duckdb
    with duckdb.connect(str(db_path)) as con:
        con.execute('CREATE SCHEMA IF NOT EXISTS audit')
        con.execute('''CREATE TABLE IF NOT EXISTS audit.release_runs (
            release_id VARCHAR, checked_at TIMESTAMP, status VARCHAR,
            validation_run_id VARCHAR, transform_run_id VARCHAR,
            review_sha256 VARCHAR, tables_count INTEGER, message VARCHAR
        )''')
        con.execute('INSERT INTO audit.release_runs VALUES (?,?,?,?,?,?,?,?)', [
            release.get('release_id', ''), datetime.now(timezone.utc).replace(tzinfo=None),
            status, release.get('validation_run_id'), release.get('transform_run_id'),
            release.get('review_sha256'), len(release.get('tables', [])), error[:4000],
        ])


def _swap_release(staging: Path, destination: Path, report_source: Path,
                  report_target: Path, *, inject_failure=None):
    """Renombrar directorio completo y restaurar ante fallos de escritura.

    No garantiza atomicidad ante apagón justo entre dos renombrados. En ese caso
    conservar .clean_previous_<ID> permite recuperación manual verificada.
    """
    data_dir = destination.parent
    old = data_dir / f'.clean_previous_{uuid4().hex}'
    moved_previous = False
    moved_new = False
    prior_report = report_target.read_bytes() if report_target.exists() else None
    try:
        if destination.exists():
            # No sobrescribir artefactos desconocidos ni borrar carpetas ajenas.
            names = {p.name for p in destination.iterdir()}
            known = {f'{t}.parquet' for t in TABLES} | {RELEASE_FILE, RELEASE_REPORT, TEMPORAL_SIDECAR, '.gitkeep'}
            if not names <= known:
                raise ReleaseBlocked(f'data/clean contiene archivos ajenos: {sorted(names-known)}')
            if any(name.endswith('.parquet') for name in names) and RELEASE_FILE not in names:
                raise ReleaseBlocked('data/clean contiene Parquet sin manifiesto verificable')
            destination.rename(old)
            moved_previous = True
        if inject_failure:
            inject_failure()
        staging.rename(destination)
        moved_new = True
        report_target.parent.mkdir(parents=True, exist_ok=True)
        # La copia final al reporte independiente también debe salir bien.
        report_tmp = report_target.with_name(f'.{report_target.name}.{uuid4().hex}.tmp')
        try:
            shutil.copyfile(report_source if report_source.exists() else destination / RELEASE_REPORT,
                            report_tmp)
            os.replace(report_tmp, report_target)
        finally:
            report_tmp.unlink(missing_ok=True)
        if moved_previous:
            try:
                shutil.rmtree(old)
            except OSError:
                # Publicación confirmada: la limpieza fallida del backup
                # no puede desencadenar un rollback de archivos ya publicados.
                LOG.warning('Backup previo conservado para revisión: %s', old)
    except Exception:
        if moved_new and destination.exists():
            # Contiene un lote nuevo completo; retirar antes de restaurar el anterior.
            shutil.rmtree(destination)
        if moved_previous and old.exists():
            old.rename(destination)
        try:
            if prior_report is None:
                report_target.unlink(missing_ok=True)
            else:
                report_target.write_bytes(prior_report)
        except OSError:
            LOG.exception('No se pudo restaurar el informe anterior: revisar manualmente')
        raise


def publish_validated(*, db_path=DB_PATH, report_dir=QUALITY_REPORTS_DIR,
                      clean_dir=DATA_CLEAN_DIR, validation: dict, review: dict,
                      gate: dict | None = None, authorization_path=None,
                      publish=False) -> dict:
    """Sólo `publish=True` permite exportar; los demás casos no escriben Parquet."""
    db_path, report_dir, clean_dir = Path(db_path), Path(report_dir), Path(clean_dir)
    if not db_path.is_file():
        raise FileNotFoundError(f'No existe la base de DuckDB: {db_path}')
    gate = decide_release_gate(validation, review)  # no confiar en un gate externo
    reasons = []
    if gate['status'] == 'BLOCKED':
        reasons.append('Persisten fallos críticos o excepciones temporales sin aceptación válida')
    if gate['status'] not in ('READY_FOR_PHASE7', 'READY_FOR_PHASE7_WITH_EXCEPTIONS', 'BLOCKED'):
        reasons.append(f"Estado de gate no reconocido: {gate['status']}")
    if gate['unexempted_critical_checks']:
        reasons.append('Controles críticos no exceptuables: '
                       + ', '.join(gate['unexempted_critical_checks']))
    auth = None
    if gate['status'] == 'READY_FOR_PHASE7_WITH_EXCEPTIONS':
        try:
            auth = validate_authorization(Path(authorization_path) if authorization_path else None,
                                          review=review, gate=gate)
        except (ReleaseBlocked, ValueError, json.JSONDecodeError) as exc:
            reasons.append(str(exc))
    _preflight_report(report_dir, validation, gate, review,
                      auth_needed=gate['status']=='READY_FOR_PHASE7_WITH_EXCEPTIONS',
                      errors=reasons)
    if reasons:
        return {'status': 'BLOCKED', 'published': False, 'reasons': reasons,
                'validation_run_id': validation['validation_run_id'],
                'review_sha256': review_sha256(review)}
    if not publish:
        return {'status': 'ELIGIBLE', 'published': False, 'reasons': [],
                'validation_run_id': validation['validation_run_id'],
                'review_sha256': review_sha256(review)}

    if clean_dir.resolve() == (db_path.parent.resolve()):
        raise ValueError('El destino Parquet no puede ser el directorio de la BD')
    release_id = uuid4().hex
    staging = clean_dir.parent / f'.clean_incoming_{release_id}'
    report_target = report_dir / 'phase7_quality_report.md'
    staging.mkdir(parents=True, exist_ok=False)
    marker = clean_dir.parent / '.clean_publication.lock'
    fd = None
    manifest = {'release_id': release_id, 'created_at_utc': utc_text(),
                'validation_run_id': validation['validation_run_id'],
                'transform_run_id': validation.get('transform_run_id'),
                'gate_status': gate['status'], 'status': 'PUBLISHED',
                'review_sha256': review_sha256(review),
                'authorization_sha256': file_sha256(Path(authorization_path)) if auth else None,
                'temporal_policy': None, 'tables': []}
    try:
        # Bloqueo de concurrencia local. Nunca eliminar lock ajeno automáticamente.
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            fd = None
            handle.write(f'{release_id}\n{utc_text()}\n')
        import duckdb
        con = duckdb.connect(str(db_path), read_only=True)
        try:
            con.execute('BEGIN TRANSACTION')
            counts = _snapshot_counts(con)
            _compare_validated_counts(con, validation, counts)
            rules = _extract_rules(con, validation.get('transform_run_id'))
            records = _stage_parquet(con, staging, counts)
            sidecar = _stage_temporal_sidecar(con, staging, validation.get('transform_run_id'),
                                               gate.get('policy_treated_events', 0))
            con.execute('COMMIT')
        finally:
            con.close()  # Liberar handle antes de registrar AUDIT
        manifest['tables'] = records
        manifest['temporal_policy'] = sidecar
        (staging / RELEASE_FILE).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        (staging / RELEASE_REPORT).write_text(
            _build_report(manifest, rules, validation, gate), encoding='utf-8')
        # Registrar el lote ya verificado antes de afectar la versión pública.
        # Una avería de AUDIT en este punto no deja datos parcialmente publicados.
        _write_history(db_path, manifest, 'PREPARED')
        _swap_release(staging, clean_dir, staging / RELEASE_REPORT, report_target)
        try:
            _write_history(db_path, manifest, 'PUBLISHED')
        except Exception:
            LOG.exception('Archivos publicados pero falló el registro final de AUDIT; '
                          'conservar manifest y entrada PREPARED para conciliación manual')
        return {'status': 'PUBLISHED', 'published': True,
                'release_id': release_id, 'report': str(report_target),
                'manifest': str(clean_dir / RELEASE_FILE), 'tables': records}
    except Exception as exc:
        try:
            _write_history(db_path, manifest, 'FAILED', str(exc))
        except Exception:
            LOG.exception('No se pudo registrar error en bitácora de publicación')
        raise
    finally:
        if fd is not None:
            os.close(fd)
        if staging.exists():
            shutil.rmtree(staging)
        # No eliminar un lock creado por otra ejecución.
        if marker.exists():
            try:
                if marker.read_text(encoding='utf-8').startswith(release_id + '\n'):
                    marker.unlink()
            except OSError:
                LOG.warning('Revisar lock de publicación: %s', marker)


def run_release(*, db_path=DB_PATH, report_dir=QUALITY_REPORTS_DIR,
                clean_dir=DATA_CLEAN_DIR, register_path=None,
                authorization_path=None, publish=False) -> dict:
    """Validación y registro frescos para que no se use un reporte antiguo."""
    validation = validate_clean(db_path=db_path, report_dir=report_dir)
    review = assess_temporal_exceptions(db_path=db_path, report_dir=report_dir,
                                        register_path=register_path)
    return publish_validated(db_path=db_path, report_dir=report_dir,
                             clean_dir=clean_dir, validation=validation, review=review,
                             authorization_path=authorization_path, publish=publish)


def main():
    parser = argparse.ArgumentParser(description='Fase 7 Olist: comprobación y publicación Parquet controlada')
    parser.add_argument('--db', type=Path, default=DB_PATH)
    parser.add_argument('--reports', type=Path, default=QUALITY_REPORTS_DIR)
    parser.add_argument('--output', type=Path, default=DATA_CLEAN_DIR)
    parser.add_argument('--register', type=Path, default=None)
    parser.add_argument('--authorization', type=Path, default=None)
    parser.add_argument('--publish', action='store_true', help='Solicitar publicación REAL, sólo si el gate lo permite')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s | %(message)s')
    try:
        result = run_release(db_path=args.db, report_dir=args.reports, clean_dir=args.output,
                             register_path=args.register, authorization_path=args.authorization,
                             publish=args.publish)
    except Exception as exc:
        print('[ERROR] Fallo técnico de publicación:', exc)
        raise SystemExit(1) from exc
    print('[FASE 7]', result['status'])
    if result['status'] == 'BLOCKED':
        for reason in result['reasons']:
            print('[BLOQUEO]', reason)
        print('[INFO] No se generó ni sustituyó ningún archivo Parquet.')
        raise SystemExit(2)
    if result['published']:
        print('[MANIFEST]', result['manifest'])
        print('[REPORTE]', result['report'])
    else:
        print('[PREVIA] Calidad apta; falta ejecutar --publish para generar Parquet.')


if __name__ == '__main__':
    main()
