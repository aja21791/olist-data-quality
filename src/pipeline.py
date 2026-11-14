"""Fase 6: orquestación íntegra, trazable y sin publicación.

Orden obligatorio: CSV -> RAW -> diagnóstico -> 15 reglas/CLEAN -> validación
-> revisión temporal -> decisión. No modifica las políticas de Fase 5 y NO
exporta Parquet: los fallos originales permanecen en el reporte de validación.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from .config import DB_PATH, FIGURES_DIR, QUALITY_REPORTS_DIR
from .diagnose import diagnose_raw
from .exception_review import EVENT_TYPES, assess_temporal_exceptions
from .extract import load_raw
from .transform import transform_data
from .validate import validate_clean

LOG = logging.getLogger(__name__)
TEMPORAL_CHECKS = set(EVENT_TYPES.values())


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _connection(db_path: Path):
    import duckdb
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(db_path))


def create_pipeline_audit(db_path: Path):
    with _connection(db_path) as con:
        con.execute('CREATE SCHEMA IF NOT EXISTS audit')
        con.execute('''CREATE TABLE IF NOT EXISTS audit.pipeline_runs (
            run_id VARCHAR PRIMARY KEY, started_at TIMESTAMP, finished_at TIMESTAMP,
            status VARCHAR, mode VARCHAR, validation_run_id VARCHAR,
            critical_failures BIGINT, unresolved_critical_events BIGINT,
            error_message VARCHAR
        )''')
        con.execute('''CREATE TABLE IF NOT EXISTS audit.pipeline_steps (
            run_id VARCHAR, step_name VARCHAR, started_at TIMESTAMP,
            finished_at TIMESTAMP, elapsed_seconds DOUBLE,
            status VARCHAR, message VARCHAR
        )''')


def _save_run(db_path, run_id, *, status=None, mode=None, validation_id=None,
              critical_failures=None, unresolved=None, error=None, initial=False):
    with _connection(db_path) as con:
        if initial:
            con.execute('''INSERT INTO audit.pipeline_runs
                VALUES (?, ?, NULL, 'RUNNING', ?, NULL, NULL, NULL, NULL)''',
                [run_id, utc_now(), mode])
        else:
            con.execute('''UPDATE audit.pipeline_runs
                SET finished_at=?, status=?, validation_run_id=?,
                    critical_failures=?, unresolved_critical_events=?, error_message=?
                WHERE run_id=?''',
                [utc_now(), status, validation_id, critical_failures, unresolved,
                 str(error)[:4000] if error else None, run_id])


def _step(db_path: Path, run_id: str, name: str, func):
    started = utc_now()
    tick = perf_counter()
    LOG.info('INICIO | %s', name)
    try:
        result = func()
    except Exception as exc:
        _save_step(db_path, run_id, name, started, perf_counter() - tick,
                   'FAILED', f'{type(exc).__name__}: {exc}')
        LOG.exception('FALLO | %s', name)
        raise
    _save_step(db_path, run_id, name, started, perf_counter() - tick, 'COMPLETED', '')
    LOG.info('OK | %s', name)
    return result


def _save_step(db_path, run_id, name, started, seconds, status, message):
    with _connection(db_path) as con:
        con.execute('''INSERT INTO audit.pipeline_steps
            VALUES (?, ?, ?, ?, ?, ?, ?)''',
            [run_id, name, started, utc_now(), round(seconds, 4), status,
             str(message)[:4000]])


def decide_release_gate(validation: dict, review: dict) -> dict:
    """Cálculo independiente de aptitud, sin falsear los controles crudos de Fase 5.

    Una anomalía temporal puede tener resolución documental, pero permanece
    contada en phase5_validation_results.csv. Las otras fallas críticas nunca
    se exceptúan aquí. No autoriza exportación: sólo prepara Fase 7.
    """
    checks = [r for r in validation['checks'] if r['severity'] == 'CRITICAL'
              and r['status'] == 'FAIL']
    raw_failures = len(checks)
    if raw_failures != int(validation['critical_failures']):
        raise RuntimeError('No coincide el número de FAIL críticos reportados')
    counts_by_type = {}
    accepted_by_check = review.get('accepted_by_check', {})
    unknown_failures = []
    unresolved = 0
    accepted = 0
    for row in checks:
        name, violations = row['check_id'], int(row['violations'])
        if name not in TEMPORAL_CHECKS:
            unknown_failures.append(name)
            continue
        actual = sum(1 for event in review['details'] if event['check_id'] == name)
        # El registro SQL y el validador deben concordar. No aprobar ante divergencias.
        if actual != violations:
            unknown_failures.append(f'{name}: validation={violations}, review={actual}')
            continue
        n_accepted = int(accepted_by_check.get(name, 0))
        if n_accepted > violations:
            raise RuntimeError(f'Aprobaciones exceden eventos observados: {name}')
        unresolved += violations - n_accepted
        accepted += n_accepted
        counts_by_type[name] = {
            'raw_violations': violations, 'formally_accepted': n_accepted,
            'unresolved': violations - n_accepted,
        }
    # Pueden existir eventos cuyo control haya aprobado por una discrepancia de SQL.
    for name in TEMPORAL_CHECKS:
        current = sum(1 for event in review['details'] if event['check_id'] == name)
        validated = next((int(row['violations']) for row in validation['checks']
                          if row['check_id'] == name), None)
        if validated is None or current != validated:
            unknown_failures.append(f'Control temporal no concilia: {name}')
    if review.get('stale_register_rows', 0):
        unknown_failures.append('Registro de revisión contiene decisiones de eventos inexistentes')
    if review.get('policy_invalid_events', 0) or review.get('policy_missing_register', 0):
        unknown_failures.append('Registro documental R05 desactualizado o incompleto: '
                                f"invalidos={review.get('policy_invalid_events', 0)}, "
                                f"faltantes={review.get('policy_missing_register', 0)}")
    status = ('BLOCKED' if unknown_failures or unresolved else
              'READY_FOR_PHASE7_WITH_EXCEPTIONS' if accepted else
              'READY_FOR_PHASE7')
    return {
        'status': status,
        'phase5_original_status': validation['status'],
        'phase5_raw_critical_failed_checks': raw_failures,
        'phase5_raw_warning_checks': int(validation['warning_checks']),
        'formally_accepted_temporal_events': accepted,
        'policy_treated_events': review.get('policy_treated_events', 0),
        'policy_source_investigation_pending': review.get('policy_source_investigation_pending', 0),
        'unresolved_critical_events': unresolved,
        'unexempted_critical_checks': unknown_failures,
        'temporal_check_details': counts_by_type,
        'review_register_pending': review['pending'],
        'review_register_invalid': review['invalid'],
        'review_register_rejected': review['rejected'],
        'review_register_stale': review.get('stale_register_rows', 0),
        'notice': ('Este es un dictamen preparatorio para la Fase 7. '
                   'La validación cruda permanece intacta, y NO se exportan Parquet. '
                   'Las aprobaciones son declaraciones documentales cuyo respaldo '
                   'debe ser verificado por un responsable real.'),
    }


def _export_gate(report_dir, pipeline_run_id, stages, validation, gate):
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        'pipeline_run_id': pipeline_run_id,
        'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'validation_run_id': validation['validation_run_id'],
        'steps': stages, 'release_gate': gate,
    }
    (report_dir / 'phase6_pipeline_summary.json').write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
    lines = [
        '# Fase 6 — Informe de ejecución y control de calidad', '',
        f"- Ejecución: `{pipeline_run_id}`",
        f"- Validación formal Fase 5: `{validation['validation_run_id']}`",
        f"- Estado Fase 5 (sin cambios): **{gate['phase5_original_status']}**",
        f"- Dictamen de la orquestación: **{gate['status']}**", '',
        '## Etapas', '',
        '| Etapa | Resultado |', '|---|---|',
    ]
    lines.extend(f"| {s['name']} | {s['status']} |" for s in stages)
    lines += ['', '## Control temporal', '',
              f"- Errores críticos originales de Fase 5: {gate['phase5_raw_critical_failed_checks']}",
              f"- Eventos temporales con excepción formal válida: {gate['formally_accepted_temporal_events']}",
              f"- Eventos temporales críticos no resueltos: {gate['unresolved_critical_events']}",
              f"- Revisión pendiente: {gate['review_register_pending']}",
              f"- Revisiones inválidas: {gate['review_register_invalid']}",
              f"- Revisiones rechazadas: {gate['review_register_rejected']}",
              f"- Registros obsoletos: {gate['review_register_stale']}", '',
              '## Restricciones', '',
              '- RAW y CLEAN no reciben alteraciones durante el control de excepciones.',
              '- Los valores temporales originales siguen visibles en Fase 5.',
              '- La aprobación de una excepción es documental, no una corrección de los datos.',
              '- No se genera ni publica ningún Parquet en la Fase 6.',
              '- Si cualquier control crítico distinto del temporal falla, la publicación sigue bloqueada.',
              '', '## Siguiente paso', '',
              'Sólo la Fase 7 puede publicar tras repetir y verificar las pruebas críticas '
              'y revisar formalmente todas las excepciones aplicables.', '',
    ]
    path = report_dir / 'phase6_pipeline_report.md'
    path.write_text('\n'.join(lines), encoding='utf-8')
    return path


def run_pipeline(db_path=DB_PATH, report_dir=QUALITY_REPORTS_DIR, *,
                 input_dir=None, figures=True, validate_only=False,
                 register_path=None, publish_requested=False,
                 authorization_path=None, clean_dir=None) -> dict:
    """Procesamiento repetible. BLOCKED se considera ejecución completa pero no publicable."""
    from .config import DATA_RAW_DIR
    db_path = Path(db_path)
    report_dir = Path(report_dir)
    input_dir = Path(input_dir) if input_dir else DATA_RAW_DIR
    run_id = uuid4().hex
    create_pipeline_audit(db_path)
    _save_run(db_path, run_id, initial=True,
              mode='VALIDATE_ONLY' if validate_only else 'FULL')
    stages = []
    outcome = None
    try:
        if not validate_only:
            stage_defs = [
                ('EXTRACTION_RAW', lambda: load_raw(input_dir=input_dir, db_path=db_path)),
                ('DIAGNOSIS_RAW', lambda: diagnose_raw(
                    db_path=db_path, report_dir=report_dir,
                    fig_dir=FIGURES_DIR, create_figures=figures)),
                ('TRANSFORMATION_CLEAN', lambda: transform_data(
                    db_path=db_path, report_dir=report_dir)),
            ]
        else:
            stage_defs = []
        stage_defs += [
            ('VALIDATION_CLEAN', lambda: validate_clean(db_path=db_path,
                                                        report_dir=report_dir)),
            ('TEMPORAL_REVIEW', lambda: assess_temporal_exceptions(
                db_path=db_path, report_dir=report_dir,
                register_path=register_path, pipeline_run_id=run_id)),
        ]
        results = {}
        for name, function in stage_defs:
            results[name] = _step(db_path, run_id, name, function)
            stages.append({'name': name, 'status': 'COMPLETED'})
        validation = results['VALIDATION_CLEAN']
        review = results['TEMPORAL_REVIEW']
        gate = _step(db_path, run_id, 'RELEASE_GATE',
                     lambda: decide_release_gate(validation, review))
        stages.append({'name': 'RELEASE_GATE', 'status': gate['status']})
        release = None
        final_status = gate['status']
        if publish_requested:
            # Importación diferida para evitar ciclo entre src.pipeline y src.release.
            from .release import publish_validated
            from .config import DATA_CLEAN_DIR
            release = _step(db_path, run_id, 'PUBLICATION_PHASE7',
                            lambda: publish_validated(
                                db_path=db_path, report_dir=report_dir,
                                clean_dir=clean_dir or DATA_CLEAN_DIR,
                                validation=validation, review=review, gate=gate,
                                authorization_path=authorization_path, publish=True))
            final_status = release['status']
            stages.append({'name': 'PUBLICATION_PHASE7', 'status': final_status})
        path = _export_gate(report_dir, run_id, stages, validation, gate)
        _save_run(db_path, run_id, status=final_status,
                  validation_id=validation['validation_run_id'],
                  critical_failures=validation['critical_failures'],
                  unresolved=gate['unresolved_critical_events'])
        outcome = {'pipeline_run_id': run_id, 'status': final_status,
                   'gate': gate, 'report': str(path), 'validation': validation,
                   'review': review, 'release': release}
        return outcome
    except Exception as exc:
        _save_run(db_path, run_id, status='FAILED', error=exc)
        raise


def main():
    parser = argparse.ArgumentParser(description='Olist: ETL con control de calidad; publicar sólo con --publish y gate conforme')
    parser.add_argument('--db', type=Path, default=DB_PATH)
    parser.add_argument('--reports', type=Path, default=QUALITY_REPORTS_DIR)
    parser.add_argument('--input', type=Path, default=None, help='Ruta de los nueve CSV originales')
    parser.add_argument('--register', type=Path, default=None, help='CSV de revisión temporal aprobado manualmente')
    parser.add_argument('--no-figures', action='store_true')
    parser.add_argument('--validate-only', action='store_true', help='Revalidar CLEAN sin repetir extracción/transformación')
    parser.add_argument('--publish', action='store_true', help='Fase 7: solicitar publicación Parquet (sujeta a gate)')
    parser.add_argument('--authorization', type=Path, default=None, help='Autorización externa de excepciones aceptadas')
    parser.add_argument('--output', type=Path, default=None, help='Directorio data/clean para Parquet')
    parser.add_argument('--evaluate', action='store_true',
                        help='Fase 8: evaluar RAW/CLEAN/Parquet sólo tras publicación confirmada')
    parser.add_argument('--deep-compare', action='store_true',
                        help='Fase 8: comparar multiconjuntos de filas Parquet y CLEAN')
    arguments = parser.parse_args()
    if arguments.evaluate and not arguments.publish:
        parser.error('--evaluate requiere --publish; no se evalúa un release no publicado')
    if arguments.deep_compare and not arguments.evaluate:
        parser.error('--deep-compare requiere --evaluate')
    logging.basicConfig(level=logging.INFO, format='%(levelname)s | %(message)s')
    try:
        result = run_pipeline(db_path=arguments.db, report_dir=arguments.reports,
                              input_dir=arguments.input, figures=not arguments.no_figures,
                              validate_only=arguments.validate_only,
                              register_path=arguments.register,
                              publish_requested=arguments.publish,
                              authorization_path=arguments.authorization,
                              clean_dir=arguments.output)
    except Exception as exc:
        print(f'[ERROR] Fallo técnico: {exc}')
        raise SystemExit(1) from exc
    print(f"\n[FASE 6] {result['status']}")
    print('[INFORME]', result['report'])
    print('[PARQUET]', 'Publicado' if result.get('release') and result['release'].get('published') else 'No publicado')
    if result['status'] == 'BLOCKED':
        print('[BLOQUEADO] Existen anomalías críticas sin resolver o aprobación documental incompleta.')
        raise SystemExit(2)
    if arguments.evaluate:
        if result['status'] != 'PUBLISHED':
            print('[FASE 8][BLOQUEADO] El pipeline no publicó un release aprobado.')
            raise SystemExit(2)
        from .evaluate import evaluate_project
        from .config import DATA_CLEAN_DIR, DICTIONARY_DIR
        try:
            final = evaluate_project(
                db_path=arguments.db, parquet_dir=arguments.output or DATA_CLEAN_DIR,
                report_dir=arguments.reports, dictionary_dir=DICTIONARY_DIR,
                figures_dir=FIGURES_DIR, create_figures=not arguments.no_figures,
                deep_compare=arguments.deep_compare)
        except Exception as exc:
            print(f'[FASE 8][ERROR] {exc}')
            raise SystemExit(1) from exc
        print(f"[FASE 8] {final['evaluation_status']} | {final['report']}")


if __name__ == '__main__':
    main()
