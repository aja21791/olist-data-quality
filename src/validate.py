"""Fase 5: comprobaciones formales, no destructivas, de RAW/CLEAN/AUDIT.

Ejecución: python -m src.validate

- SQL en sql/validations/*.sql; Python orquesta, reporta y audita.
- No transforma RAW/CLEAN, no exporta Parquet, no crea STAGING persistente.
- Un FAIL crítico bloquea la publicación en las siguientes fases.
- Las advertencias se conservan para revisión, no se 'arreglan' automáticamente.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config import DB_PATH, QUALITY_REPORTS_DIR, ROOT_DIR
from .extract import qi
from .model_metadata import RELATIONSHIPS
from .transform_schema import (
    SCHEMA, KEYS, REQUIRED, TIMESTAMPS, MONEYS, INTEGERS,
    DOUBLES, ZIP_FIELDS,
)

LOG = logging.getLogger(__name__)
SQL_DIR = ROOT_DIR / 'sql' / 'validations'
TYPE_MAP = {
    **{field: 'TIMESTAMP' for field in TIMESTAMPS},
    **{field: 'DECIMAL(18,2)' for field in MONEYS},
    **{field: 'INTEGER' for field in INTEGERS},
    **{field: 'DOUBLE' for field in DOUBLES},
}
PAYMENT_TYPES = ('credit_card', 'boleto', 'voucher', 'debit_card', 'not_defined')
ORDER_STATUSES = ('created', 'approved', 'invoiced', 'processing', 'shipped',
                  'delivered', 'unavailable', 'canceled')


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def fetch_count(con, statement: str, params=None) -> int:
    return int(con.execute(statement, params or []).fetchone()[0])


def sql_file(name: str) -> str:
    return (SQL_DIR / name).read_text(encoding='utf-8').strip().rstrip(';')


def query(con, name: str, **sql_args):
    """Interpolar sólo identificadores permitidos y predicados definidos en este módulo."""
    statement = sql_file(name).format(**sql_args)
    return con.execute(statement).fetchone()


def table_names(con, schema: str) -> set[str]:
    return {r[0] for r in con.execute(
        'SELECT table_name FROM information_schema.tables WHERE table_schema=?',
        [schema],
    ).fetchall()}


def create_validation_audit(con):
    con.execute('CREATE SCHEMA IF NOT EXISTS audit')
    con.execute('''CREATE TABLE IF NOT EXISTS audit.validation_runs (
        validation_run_id VARCHAR PRIMARY KEY,
        transform_run_id VARCHAR,
        started_at TIMESTAMP,
        finished_at TIMESTAMP,
        status VARCHAR,
        critical_failures BIGINT,
        warning_checks BIGINT,
        error_message VARCHAR
    )''')
    con.execute('''CREATE TABLE IF NOT EXISTS audit.validation_results (
        validation_run_id VARCHAR,
        check_id VARCHAR,
        rule_id VARCHAR,
        table_name VARCHAR,
        severity VARCHAR,
        status VARCHAR,
        violations BIGINT,
        population BIGINT,
        explanation VARCHAR,
        checked_at TIMESTAMP
    )''')


def write_csv(path: Path, rows: list[dict], fields: list[str]):
    with path.open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: ('' if row.get(k) is None else row[k]) for k in fields}
                         for row in rows)


def export_validation_report(path: Path, outcome: dict):
    path.mkdir(parents=True, exist_ok=True)
    results = outcome['checks']
    fields = ['validation_run_id', 'check_id', 'rule_id', 'table_name', 'severity',
              'status', 'violations', 'population', 'explanation', 'checked_at']
    write_csv(path / 'phase5_validation_results.csv', results, fields)
    write_csv(path / 'phase5_payment_exceptions.csv', outcome['payment_exceptions'],
              ['order_id', 'item_total', 'paid_total', 'situation', 'difference'])
    (path / 'phase5_validation_summary.json').write_text(
        json.dumps(outcome, ensure_ascii=False, default=str, indent=2), encoding='utf-8'
    )
    lines = [
        '# Fase 5 — Validación formal de RAW, CLEAN y AUDIT', '',
        f"- Validación: `{outcome['validation_run_id']}`",
        f"- Transformación evaluada: `{outcome['transform_run_id']}`",
        f"- Fecha UTC: `{outcome['checked_at_utc']}`",
        f"- Estado: **{outcome['status']}**",
        f"- Pruebas críticas fallidas: **{outcome['critical_failures']}**",
        f"- Pruebas con advertencia: **{outcome['warning_checks']}**", '',
        '**Política de aprobación:** un `FAIL` de severidad `CRITICAL` bloquea la '
        'publicación. Las advertencias quedan visibles pero no se eliminan automáticamente.',
        '**Importante:** esta fase no exporta Parquet ni afirma exactitud frente a '
        'fuentes externas. RAW y CLEAN no se modifican.', '',
        '## Resultados por prueba', '',
        '| Prueba | Regla | Tabla/campo | Nivel | Resultado | Incumplimientos | Universo |',
        '|---|---|---|---|---|---:|---:|',
    ]
    for r in results:
        lines.append('| {check_id} | {rule_id} | {table_name} | {severity} | {status} '
                     '| {violations} | {population} |'.format(**r))
    lines += ['', '## Pruebas que requieren atención', '']
    issues = [r for r in results if r['status'] != 'PASS']
    if issues:
        for row in issues:
            lines.append(f"- **{row['check_id']} [{row['status']}]** — "
                         f"{row['table_name']}: {row['violations']} casos. "
                         f"{row['explanation']}")
    else:
        lines.append('No se detectaron incumplimientos en las pruebas configuradas.')
    lines += ['', '## Alcance y límites', '',
              '- Referencias principales, claves y campos obligatorios: críticos.',
              '- Fechas temporalmente imposibles, dominios numéricos y monetarios: críticos.',
              '- Diferencias entre pagos y artículos, métodos desconocidos, falta de '
              'traducciones y valores IQR: advertencias sujetas a análisis del negocio.',
              '- La conciliación agrupa importes por `order_id` antes de unir tablas.',
              '- Se respeta la granularidad de reseñas, artículos, pagos y geolocalización.',
              '- La igualdad de conteos RAW=CLEAN+cuarentena no garantiza que el '
              'contenido RAW no se modificó; para ello se requeriría contrastar '
              'huellas de todas las filas con las del momento de la extracción.',
              '- Las pruebas no sustituyen la verificación de exactitud con una fuente externa.',
              '- Las excepciones pueden ser legítimas: un WARNING no implica corregir valores.',
              '', '## Decisión de salida', '',
              ('**BLOQUEADO:** revisar pruebas críticas antes de plantear exportación.'
               if outcome['critical_failures'] else
               '**APROBADO PARA CONTINUAR:** pasar a Fase 6 (automatización); la '
               'publicación de Parquet se realizará únicamente en la Fase 7.'),
              '']
    output = path / 'phase5_validation_report.md'
    output.write_text('\n'.join(lines), encoding='utf-8')
    return output


def validate_clean(db_path=DB_PATH, report_dir=QUALITY_REPORTS_DIR) -> dict:
    """Validar sin cambiar RAW ni CLEAN. Guardar los resultados incluso si hay FAIL."""
    import duckdb
    db_path, report_dir = Path(db_path), Path(report_dir)
    if not db_path.is_file():
        raise FileNotFoundError(f'No existe {db_path}. Ejecuta las Fases 2 y 4.')
    con = duckdb.connect(str(db_path))
    validation_id = uuid4().hex
    started = utc_now()
    rows: list[dict] = []
    samples: list[dict] = []
    last_transform_id = None

    def add(check_id, rule, table, severity, violations, population, explanation):
        if severity not in ('CRITICAL', 'WARNING'):
            raise ValueError('Severidad desconocida')
        violations = int(violations)
        population = int(population)
        if violations < 0 or population < 0:
            raise ValueError('Conteos negativos inválidos')
        state = ('FAIL' if severity == 'CRITICAL' else 'WARNING') if violations else 'PASS'
        row = dict(validation_run_id=validation_id, check_id=check_id,
                   rule_id=rule, table_name=table, severity=severity,
                   status=state, violations=violations, population=population,
                   explanation=explanation, checked_at=utc_now())
        rows.append(row)
        LOG.info('%-7s %-32s %8s | %s', state, check_id, violations, table)

    def count_predicate(table: str, condition: str) -> int:
        return int(query(con, '04_predicate.sql', table=qi(table), condition=condition)[0])

    try:
        create_validation_audit(con)
        con.execute('INSERT INTO audit.validation_runs VALUES (?,?,?,NULL,?,NULL,NULL,NULL)',
                    [validation_id, None, started, 'RUNNING'])
        schemas = {r[0] for r in con.execute('SELECT schema_name FROM information_schema.schemata').fetchall()}
        add('V00.RAW_SCHEMA', 'R15', 'raw', 'CRITICAL', int('raw' not in schemas), 1,
            'Debe existir esquema RAW persistente')
        add('V00.CLEAN_SCHEMA', 'R15', 'clean', 'CRITICAL', int('clean' not in schemas), 1,
            'Debe existir esquema CLEAN persistente')
        raw_tables = table_names(con, 'raw')
        clean_tables = table_names(con, 'clean')
        audit_tables = table_names(con, 'audit')
        needed_audit = {'transform_runs', 'rule_results', 'quarantine_records', 'quality_rule_catalog'}
        add('V00.AUDIT_OBJECTS', 'R15', 'audit', 'CRITICAL',
            len(needed_audit - audit_tables), len(needed_audit),
            'Se requieren tablas de auditoría generadas por src.transform')
        if 'transform_runs' in audit_tables:
            last = con.execute('''SELECT run_id FROM audit.transform_runs
                WHERE status='COMPLETED' ORDER BY finished_at DESC NULLS LAST, run_id DESC
                LIMIT 1''').fetchone()
            if last:
                last_transform_id = str(last[0])
        add('V00.TRANSFORM_COMPLETED', 'R15', 'audit', 'CRITICAL',
            int(last_transform_id is None), 1,
            'La validación exige una transformación de Fase 4 confirmada')
        if last_transform_id and 'rule_results' in audit_tables:
            n = fetch_count(con, '''SELECT COUNT(DISTINCT rule_id) FROM audit.rule_results
                WHERE run_id=?''', [last_transform_id])
            add('V00.CATALOG_15', 'R15', 'audit', 'CRITICAL', abs(15-n), 15,
                'La ejecución debe registrar las 15 reglas distintas')
            bad = fetch_count(con, '''SELECT COUNT(*) FROM audit.rule_results
                WHERE run_id=? AND status='FAIL' ''', [last_transform_id])
            add('V00.PHASE4_FAIL', 'R15', 'audit', 'CRITICAL', bad, 15,
                'Ningún fallo de la Fase 4 debe quedar sin resolver')
        if last_transform_id and 'quarantine_records' in audit_tables:
            qcount = fetch_count(con, 'SELECT COUNT(*) FROM audit.quarantine_records WHERE run_id=?',
                                 [last_transform_id])
            add('V00.QUARANTINE_REVIEW', 'R15', 'audit', 'WARNING', qcount, qcount,
                'Registros aislados por la transformación: revisar motivos y trazabilidad')

        available_clean = set()
        for table, fields in SCHEMA.items():
            raw_exists = table in raw_tables
            clean_exists = table in clean_tables
            add(f'V01.RAW.{table}', 'R15', table, 'CRITICAL', int(not raw_exists), 1,
                'La tabla RAW de la fuente debe existir')
            add(f'V01.CLEAN.{table}', 'R15', table, 'CRITICAL', int(not clean_exists), 1,
                'La tabla CLEAN transformada debe existir')
            if not clean_exists:
                continue
            actual = {r[0]: r[1].upper().replace(' ', '') for r in con.execute(
                f'DESCRIBE clean.{qi(table)}').fetchall()}
            missing = set(fields) - set(actual)
            unexpected = set(actual) - set(fields)
            mismatches = [col for col in fields if col in actual and
                          actual[col] != TYPE_MAP.get((table, col), 'VARCHAR')]
            issues = len(missing) + len(unexpected) + len(mismatches)
            add(f'V02.SCHEMA.{table}', 'R04', table, 'CRITICAL', issues, len(fields),
                'Columnas y tipos deben coincidir exactamente con transform_schema.py. '
                f'Faltantes={sorted(missing)}; extras={sorted(unexpected)}; tipos={mismatches}')
            if issues:
                continue  # no ejecutar una prueba sobre columnas inexistentes o de tipo erróneo
            available_clean.add(table)
            clean_size = fetch_count(con, f'SELECT COUNT(*) FROM clean.{qi(table)}')
            if raw_exists and last_transform_id and 'quarantine_records' in audit_tables:
                raw_size = fetch_count(con, f'SELECT COUNT(*) FROM raw.{qi(table)}')
                quarantine = fetch_count(con, '''SELECT COUNT(*) FROM audit.quarantine_records
                    WHERE source_table=? AND run_id=?''', [table, last_transform_id])
                diff = abs(raw_size - clean_size - quarantine)
                add(f'V03.BALANCE.{table}', 'R15', table, 'CRITICAL', diff, raw_size,
                    f'RAW={raw_size}; CLEAN={clean_size}; cuarentena={quarantine}; '
                    'diferencia absoluta debe ser cero')
            for col in REQUIRED.get(table, []):
                nulls = int(query(con, '02_required.sql', table=qi(table), column=qi(col))[0])
                add(f'V04.NULL.{table}.{col}', 'R02', f'{table}.{col}', 'CRITICAL',
                    nulls, clean_size, 'Campo obligatorio: no se admiten NULL')
            for col in fields:
                if (table, col) in TYPE_MAP:
                    continue
                empties = count_predicate(table, f'{qi(col)} IS NOT NULL AND TRIM({qi(col)}) = \'\'')
                is_technical_id = col.endswith('_id') or col.endswith('_unique_id')
                add(f'V05.EMPTY.{table}.{col}', 'R14', f'{table}.{col}',
                    'CRITICAL' if is_technical_id else 'WARNING', empties, clean_size,
                    'No deben quedar cadenas vacías en CLEAN')
                if is_technical_id:
                    surrounding = count_predicate(
                        table, f'{qi(col)} IS NOT NULL AND {qi(col)} <> TRIM({qi(col)})'
                    )
                    add(f'V05.ID_SPACES.{table}.{col}', 'R03', f'{table}.{col}',
                        'CRITICAL', surrounding, clean_size,
                        'Los identificadores técnicos no se normalizan silenciosamente')

        for table, columns in KEYS.items():
            if table not in available_clean:
                continue
            dup = int(query(con, '01_uniqueness.sql', table=qi(table),
                            keys=', '.join(qi(c) for c in columns))[0])
            add(f'V06.KEY.{table}', 'R01', table, 'CRITICAL', dup,
                fetch_count(con, f'SELECT COUNT(*) FROM clean.{qi(table)}'),
                f"Clave compuesta o simple: {', '.join(columns)}. "
                'No forzar unicidad en order_reviews ni en geolocation')

        for table, column in MONEYS:
            if table in available_clean:
                n = count_predicate(table, f'{qi(column)} < 0')
                add(f'V07.MONEY.{table}.{column}', 'R06', f'{table}.{column}',
                    'CRITICAL', n, fetch_count(con, f'SELECT COUNT(*) FROM clean.{qi(table)}'),
                    'Un importe negativo es inválido según la política de Fase 4; cero puede ser válido')
                outliers = int(query(con, '07_iqr.sql', table=qi(table), column=qi(column))[0])
                add(f'V07.IQR.{table}.{column}', 'R07', f'{table}.{column}',
                    'WARNING', outliers, fetch_count(con, f'SELECT COUNT(*) FROM clean.{qi(table)}'),
                    'IQR señala valores extremos, no prueba por sí solo un error')

        numeric_domains = (
            ('order_items', 'order_item_id', '"order_item_id" < 1', 'R15'),
            ('order_payments', 'payment_sequential', '"payment_sequential" < 1', 'R15'),
            ('order_payments', 'payment_installments', '"payment_installments" < 0', 'R15'),
            ('order_reviews', 'review_score', '"review_score" NOT BETWEEN 1 AND 5', 'R15'),
            ('geolocation', 'geolocation_lat', 'ABS("geolocation_lat") > 90', 'R15'),
            ('geolocation', 'geolocation_lng', 'ABS("geolocation_lng") > 180', 'R15'),
        )
        numeric_domains += tuple(
            ('products', field, f'{qi(field)} < 0', 'R15')
            for table, field in (*INTEGERS, *DOUBLES) if table == 'products'
        )
        for table, col, predicate, rule in numeric_domains:
            if table in available_clean:
                add(f'V08.DOMAIN.{table}.{col}', rule, f'{table}.{col}', 'CRITICAL',
                    count_predicate(table, predicate),
                    fetch_count(con, f'SELECT COUNT(*) FROM clean.{qi(table)}'),
                    'Validación del dominio físico o de negocio, sin imputaciones')

        if 'orders' in available_clean:
            total_orders = fetch_count(con, 'SELECT COUNT(*) FROM clean.orders')
            chronological = {
                'approval_before_purchase': ('order_approved_at IS NOT NULL AND '
                                             'order_approved_at < order_purchase_timestamp'),
                'carrier_before_purchase': ('order_delivered_carrier_date IS NOT NULL AND '
                                            'order_delivered_carrier_date < order_purchase_timestamp'),
                'delivery_before_purchase': ('order_delivered_customer_date IS NOT NULL AND '
                                             'order_delivered_customer_date < order_purchase_timestamp'),
                'delivery_before_carrier': ('order_delivered_carrier_date IS NOT NULL AND '
                                            'order_delivered_customer_date IS NOT NULL AND '
                                            'order_delivered_customer_date < order_delivered_carrier_date'),
            }
            for name, predicate in chronological.items():
                add(f'V09.TIME.{name}', 'R05', 'orders', 'CRITICAL',
                    count_predicate('orders', predicate), total_orders,
                    'Secuencia temporal imposible; revisar la fuente, no fabricar fechas')
            statuses = ','.join("'"+x+"'" for x in ORDER_STATUSES)
            for code, pred in (
                ('unknown', f'order_status IS NULL OR order_status NOT IN ({statuses})'),
                ('delivered_without_date', "order_status='delivered' AND order_delivered_customer_date IS NULL"),
                ('early_state_with_delivery', "order_status IN ('created','approved') AND order_delivered_customer_date IS NOT NULL"),
            ):
                add(f'V10.STATUS.{code}', 'R10', 'orders', 'WARNING',
                    count_predicate('orders', pred), total_orders,
                    'Estado o evento sospechoso: requiere interpretación del negocio')

        if 'order_reviews' in available_clean:
            add('V09.REVIEW.answer_before_creation', 'R05', 'order_reviews', 'CRITICAL',
                count_predicate('order_reviews', 'review_answer_timestamp IS NOT NULL '
                                'AND review_creation_date IS NOT NULL '
                                'AND review_answer_timestamp < review_creation_date'),
                fetch_count(con, 'SELECT COUNT(*) FROM clean.order_reviews'),
                'La respuesta de una reseña no debe preceder a su creación')
        if {'order_items', 'orders'} <= available_clean:
            count = fetch_count(con, '''SELECT COUNT(*) FROM clean.order_items i
                INNER JOIN clean.orders o ON i.order_id=o.order_id
                WHERE i.shipping_limit_date IS NOT NULL
                  AND i.shipping_limit_date < o.order_purchase_timestamp''')
            add('V09.SHIPPING.limit_before_purchase', 'R05', 'order_items', 'WARNING',
                count, fetch_count(con, 'SELECT COUNT(*) FROM clean.order_items'),
                'Límite de envío anterior a la compra: investigar')

        for table, column in ZIP_FIELDS:
            if table in available_clean:
                n = count_predicate(table, f'{qi(column)} IS NOT NULL AND NOT '
                                    f"regexp_full_match({qi(column)}, '[0-9]{{5}}')")
                add(f'V11.ZIP.{table}', 'R11', f'{table}.{column}', 'WARNING',
                    n, fetch_count(con, f'SELECT COUNT(*) FROM clean.{qi(table)}'),
                    'Prefijo postal brasileño: cinco dígitos, no CEP completo')

        for child, child_col, parent, parent_col, note in RELATIONSHIPS:
            if child not in available_clean or parent not in available_clean:
                continue
            orphans = int(query(con, '03_orphans.sql', child_table=qi(child),
                                child_col=qi(child_col), parent_table=qi(parent),
                                parent_col=qi(parent_col))[0])
            optional = parent == 'category_translation'
            if not optional:
                null_foreign_key = count_predicate(child, f'{qi(child_col)} IS NULL')
                add(f'V12.FK_NULL.{child}.{child_col}', 'R09',
                    f'{child}.{child_col}', 'CRITICAL', null_foreign_key,
                    fetch_count(con, f'SELECT COUNT(*) FROM clean.{qi(child)}'),
                    'La referencia obligatoria no puede estar ausente')
            add(f'V12.FK.{child}.{child_col}', 'R08' if optional else 'R09',
                f'{child}.{child_col}->{parent}.{parent_col}',
                'WARNING' if optional else 'CRITICAL', orphans,
                fetch_count(con, f'SELECT COUNT(*) FROM clean.{qi(child)}'),
                ('Traducción opcional sin coincidencia' if optional else
                 'Referencia huérfana: no se crean padres ficticios; revisar cuarentena')
                + f'. {note}')

        if 'order_payments' in available_clean:
            types_sql = ','.join("'"+x+"'" for x in PAYMENT_TYPES)
            n = count_predicate('order_payments',
                                f'payment_type IS NULL OR payment_type NOT IN ({types_sql})')
            add('V13.PAYMENT_TYPE', 'R13', 'order_payments', 'WARNING', n,
                fetch_count(con, 'SELECT COUNT(*) FROM clean.order_payments'),
                'No sustituir categorías desconocidas por un valor inventado')

        if {'orders', 'order_payments', 'order_items'} <= available_clean:
            total, pending, discrepant, reconciled, maximum = query(con, '05_payments.sql')
            add('V14.PAYMENT_PENDING', 'R12', 'orders', 'WARNING', pending, total,
                'Pedidos sin un total de ítems o sin total de pagos: revisar estados')
            add('V14.PAYMENT_DISCREPANT', 'R12', 'orders', 'WARNING', discrepant, total,
                f'Importe de artículos+flete vs pagos (tolerancia 0.01); '
                f'conciliados={reconciled}; diferencia máxima={maximum}')
            all_details = sql_file('06_payment_details.sql').format(limit=50)
            rec = con.execute(all_details)
            samples = [dict(zip([x[0] for x in rec.description], tup))
                       for tup in rec.fetchall()]

        critical = sum(1 for r in rows if r['status'] == 'FAIL')
        warnings = sum(1 for r in rows if r['status'] == 'WARNING')
        outcome_status = ('BLOCKED' if critical else
                          'PASS_WITH_WARNINGS' if warnings else 'PASS')
        con.execute('BEGIN TRANSACTION')
        try:
            con.executemany('''INSERT INTO audit.validation_results
                (validation_run_id,check_id,rule_id,table_name,severity,status,
                 violations,population,explanation,checked_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)''', [
                [r[col] for col in ('validation_run_id', 'check_id', 'rule_id', 'table_name',
                                   'severity', 'status', 'violations', 'population',
                                   'explanation', 'checked_at')]
                for r in rows
            ])
            con.execute('''UPDATE audit.validation_runs
                SET transform_run_id=?, finished_at=?,status=?,critical_failures=?,warning_checks=?
                WHERE validation_run_id=?''',
                [last_transform_id, utc_now(), outcome_status, critical, warnings, validation_id])
            con.execute('COMMIT')
        except Exception:
            con.execute('ROLLBACK')
            raise
        outcome = dict(validation_run_id=validation_id,
                       transform_run_id=last_transform_id,
                       checked_at_utc=utc_now().isoformat(), status=outcome_status,
                       critical_failures=critical, warning_checks=warnings,
                       checks=rows, payment_exceptions=samples)
        report = export_validation_report(report_dir, outcome)
        outcome['report'] = str(report)
        return outcome
    except Exception as exc:
        try:
            con.execute('''UPDATE audit.validation_runs SET status='ERROR',finished_at=?,
                error_message=? WHERE validation_run_id=?''',
                [utc_now(), str(exc)[:4000], validation_id])
        except Exception:
            pass
        raise
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser(description='Fase 5: validación no destructiva de CLEAN')
    parser.add_argument('--db', type=Path, default=DB_PATH)
    parser.add_argument('--reports', type=Path, default=QUALITY_REPORTS_DIR)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s | %(message)s')
    outcome = validate_clean(db_path=args.db, report_dir=args.reports)
    print(f"\n[RESULTADO] {outcome['status']}; FAIL críticos={outcome['critical_failures']}; "
          f"WARNING={outcome['warning_checks']}")
    print('[REPORTE] ' + outcome['report'])
    print('[INFO] RAW y CLEAN intactos. Parquet todavía no publicado.')
    if outcome['critical_failures']:
        raise SystemExit(1)  # útil para CI y para el pipeline de Fase 6


if __name__ == '__main__':
    main()
