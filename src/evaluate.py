"""Fase 8: evaluación reproducible RAW vs CLEAN y cierre documental de Olist.

No reimporta datos, no modifica RAW/CLEAN/AUDIT y no republica los Parquet.
Exige un release verificado por SHA-256, esquema y conteos (Fase 7), y
comprueba que el release corresponde a la última transformación completada.

Uso: python -m src.evaluate [--no-figures] [--deep-compare]
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .config import (DB_PATH, DATA_CLEAN_DIR, DICTIONARY_DIR, FIGURES_DIR,
                     QUALITY_REPORTS_DIR, ROOT_DIR)
from .model_metadata import BY_NAME, RELATIONSHIPS
from .release import RELEASE_FILE
from .temporal_policy import POLICY_ID
from .transform_schema import (DOUBLES, INTEGERS, KEYS, MONEYS, REQUIRED,
                               SCHEMA, TIMESTAMPS)
from .verify_release import verify_release

LOG = logging.getLogger(__name__)
SQL_DIR = ROOT_DIR / 'sql' / 'evaluation'
REQUIRED_RELEASE_STATUS = {'PASS', 'PASS_WITH_WARNINGS'}

DIMENSIONS = {
    'completeness': ('Completitud (campos obligatorios)', 'Celdas ausentes o vacías', 'celdas'),
    'uniqueness': ('Unicidad (claves verificadas)', 'Filas excedentes por clave', 'filas'),
    'monetary': ('Validez monetaria', 'Importes negativos o no convertibles', 'campos'),
    'domain': ('Validez de dominios numéricos', 'Valores fuera de rango', 'campos'),
    'temporal': ('Coherencia temporal', 'Contradicciones entre fechas', 'eventos'),
    'referential': ('Integridad referencial obligatoria', 'Referencias ausentes o huérfanas', 'relaciones'),
}

DOMAIN_RULES = (
    ('order_items', 'order_item_id', 'positive'),
    ('order_payments', 'payment_sequential', 'positive'),
    ('order_payments', 'payment_installments', 'nonnegative'),
    ('order_reviews', 'review_score', 'score'),
    ('geolocation', 'geolocation_lat', 'latitude'),
    ('geolocation', 'geolocation_lng', 'longitude'),
    *((table, col, 'nonnegative') for table, col in (*INTEGERS, *DOUBLES) if table == 'products'),
)


def qi(name: str) -> str:
    """Solo citar nombres provenientes del catálogo interno verificable."""
    return '"' + name.replace('"', '""') + '"'


def sql(name: str, **kwargs) -> str:
    return (SQL_DIR / name).read_text(encoding='utf-8').format(**kwargs)


def count(con, statement: str, params=None) -> int:
    return int(con.execute(statement, params or []).fetchone()[0])


def missing_expr(col: str, layer: str, alias: str = '') -> str:
    value = f'{alias}{qi(col)}'
    return (f"NULLIF(TRIM({value}), '') IS NULL" if layer == 'raw'
            else f"({value} IS NULL OR TRIM(CAST({value} AS VARCHAR)) = '')")


def typed_expr(col: str, layer: str, datatype: str) -> str:
    value = qi(col)
    return (f"TRY_CAST(NULLIF(TRIM({value}), '') AS {datatype})" if layer == 'raw'
            else value)


def safe_rate(issues: int, total: int):
    """None cuando no hay denominador: nunca interpretar una muestra vacía como 100% sana."""
    return round(100.0 * int(issues) / int(total), 6) if total else None


def classify_change(before: int, after: int) -> str:
    if before > after:
        return 'MENOS_INCIDENCIAS'
    if before < after:
        return 'MAS_INCIDENCIAS_REVISAR'
    return 'SIN_CAMBIO'


def metric(*, dimension, metric_id, table, column, before, after,
           universe_before, universe_after, unit, definition, note='') -> dict:
    return {
        'dimension': dimension, 'metric_id': metric_id, 'table': table, 'column': column,
        'before_issues': int(before), 'after_issues': int(after),
        'universe_before': int(universe_before), 'universe_after': int(universe_after),
        'rate_before_pct': safe_rate(before, universe_before),
        'rate_after_pct': safe_rate(after, universe_after),
        'unit': unit, 'definition': definition, 'interpretation': classify_change(before, after),
        'note': note,
    }


def _get_tabledescribe(con, layer: str, table: str) -> dict[str, str]:
    return {str(r[0]): str(r[1]) for r in con.execute(
        f'DESCRIBE {layer}.{qi(table)}').fetchall()}


def assert_release_snapshot(con, manifest: dict) -> dict:
    """Vincular publicación, transformación y validación reales de DuckDB."""
    if manifest.get('status') != 'PUBLISHED':
        raise ValueError('El manifiesto no contiene una publicación aprobada')
    latest = con.execute('''SELECT run_id FROM audit.transform_runs
        WHERE status='COMPLETED'
        ORDER BY finished_at DESC NULLS LAST, run_id DESC LIMIT 1''').fetchone()
    if not latest or latest[0] != manifest.get('transform_run_id'):
        raise ValueError('Release obsoleto: última transformación distinta del manifiesto. '
                         'Vuelve a validar y publicar Fase 7 antes de evaluar.')
    validation = con.execute('''SELECT status, critical_failures, warning_checks,
                                      transform_run_id
        FROM audit.validation_runs WHERE validation_run_id=?''',
        [manifest.get('validation_run_id')]).fetchone()
    if (not validation or validation[0] not in REQUIRED_RELEASE_STATUS
            or int(validation[1]) != 0 or validation[3] != latest[0]):
        raise ValueError('La validación vinculada al release no fue aprobada o es inconsistente')
    actual_rules = {r[0] for r in con.execute('''SELECT DISTINCT rule_id
        FROM audit.rule_results WHERE run_id=?''', [latest[0]]).fetchall()}
    if actual_rules != {f'R{i:02d}' for i in range(1, 16)}:
        raise ValueError('El release no tiene las 15 reglas registradas en auditoría')
    if len(manifest.get('tables', [])) != len(SCHEMA):
        raise ValueError('El manifiesto no contiene los nueve datasets esperados')
    for row in manifest['tables']:
        table = row['table']
        if table not in SCHEMA:
            raise ValueError(f'Tabla desconocida en manifiesto: {table}')
        actual_raw = count(con, f'SELECT COUNT(*) FROM raw.{qi(table)}')
        actual_clean = count(con, f'SELECT COUNT(*) FROM clean.{qi(table)}')
        if actual_raw != int(row['raw_rows']) or actual_clean != int(row['clean_rows']):
            raise ValueError(f'Los conteos RAW/CLEAN cambiaron desde publicación: {table}')
        live_schema = [(r[0], r[1]) for r in con.execute(
            f'DESCRIBE clean.{qi(table)}').fetchall()]
        if [list(v) for v in live_schema] != row['schema']:
            raise ValueError(f'El esquema CLEAN de {table} cambió desde la publicación')
    return {'validation_status': validation[0], 'critical_failures': int(validation[1]),
            'warning_checks': int(validation[2]), 'transform_run_id': latest[0]}


def calculate_metrics(con) -> tuple[list[dict], list[dict]]:
    """Calcular tasas con unidades explícitas, no mezclar filas, celdas y eventos."""
    output = []
    table_counts = []
    counts = {}
    for table in SCHEMA:
        raw_count = count(con, f'SELECT COUNT(*) FROM raw.{qi(table)}')
        clean_count = count(con, f'SELECT COUNT(*) FROM clean.{qi(table)}')
        counts[table] = (raw_count, clean_count)
        table_counts.append({'table': table, 'raw_rows': raw_count,
                             'clean_rows': clean_count, 'difference': raw_count - clean_count})

    def add(dim, ident, table, col, nraw, nclean, denominator_raw, denominator_clean, definition, note=''):
        output.append(metric(dimension=dim, metric_id=ident, table=table, column=col,
                             before=nraw, after=nclean,
                             universe_before=denominator_raw, universe_after=denominator_clean,
                             unit=DIMENSIONS[dim][2], definition=definition, note=note))

    # Completitud de campos obligatorios: cada campo cuenta como una oportunidad.
    for table, fields in REQUIRED.items():
        for col in fields:
            totals = []
            for layer in ('raw', 'clean'):
                row = con.execute(sql('01_field_missing.sql', layer=layer, table=qi(table),
                                      missing_predicate=missing_expr(col, layer))).fetchone()
                totals.append((int(row[1]), int(row[0])))
            add('completeness', f'MISSING.{table}.{col}', table, col,
                totals[0][0], totals[1][0], totals[0][1], totals[1][1],
                'NULL o texto vacío en campo obligatorio')

    for table, fields in KEYS.items():
        keys = ', '.join(qi(c) for c in fields)
        bad = [count(con, sql('02_key_duplicates.sql', layer=layer, table=qi(table), key_columns=keys))
               for layer in ('raw', 'clean')]
        add('uniqueness', f'KEY.{table}', table, ','.join(fields),
            *bad, *counts[table], 'Filas excedentes por clave verificada',
            'Se excluyen order_reviews y geolocation: su granularidad permite repeticiones')

    for table, col in MONEYS:
        pre = typed_expr(col, 'raw', 'DECIMAL(18,2)')
        source = f"NULLIF(TRIM({qi(col)}), '')"
        raw_condition = f'({source} IS NOT NULL AND ({pre} IS NULL OR {pre} < 0))'
        clean_condition = f'{qi(col)} < 0'
        violation = []
        for layer, condition in (('raw',raw_condition),('clean',clean_condition)):
            row = con.execute(sql('03_predicate.sql', layer=layer, table=qi(table),
                                  condition=condition)).fetchone()
            violation.append((int(row[1]),int(row[0])))
        add('monetary', f'MONEY.{table}.{col}', table, col,
            violation[0][0], violation[1][0], violation[0][1], violation[1][1],
            'RAW: importe no convertible o negativo; CLEAN: importe negativo',
            'Las conversiones imposibles pueden ir a cuarentena; no se infiere exactitud')

    for table, col, domain in DOMAIN_RULES:
        cast_type = 'DOUBLE' if (table, col) in DOUBLES else 'INTEGER'
        def bad_predicate(layer):
            typed = typed_expr(col, layer, cast_type)
            if domain == 'positive':
                invalid = f'{typed} <= 0'
            elif domain == 'nonnegative':
                invalid = f'{typed} < 0'
            elif domain == 'score':
                invalid = f'({typed} < 1 OR {typed} > 5)'
            elif domain == 'latitude':
                invalid = f'({typed} < -90 OR {typed} > 90)'
            else:
                invalid = f'({typed} < -180 OR {typed} > 180)'
            if layer == 'raw':
                original = f"NULLIF(TRIM({qi(col)}), '')"
                return f'({original} IS NOT NULL AND ({typed} IS NULL OR {invalid}))'
            return invalid
        # La consulta devuelve primero población y después incidencias.
        raw_row = con.execute(sql('03_predicate.sql', layer='raw',table=qi(table),
                                  condition=bad_predicate('raw'))).fetchone()
        clean_row = con.execute(sql('03_predicate.sql', layer='clean',table=qi(table),
                                  condition=bad_predicate('clean'))).fetchone()
        add('domain', f'DOMAIN.{table}.{col}', table, col,
            int(raw_row[1]), int(clean_row[1]), int(raw_row[0]), int(clean_row[0]),
            f'RAW: formato/rango inválido; CLEAN: rango inválido ({domain})')

    chronological = ('approval_before_purchase', 'carrier_before_purchase',
                     'delivery_before_purchase', 'delivery_before_carrier')
    chrono_results = {}
    for layer in ('raw','clean'):
        kwargs = {f'{name}_expr': typed_expr(col, layer, 'TIMESTAMP')
                  for name, col in (('purchase','order_purchase_timestamp'),
                                    ('approval','order_approved_at'),
                                    ('carrier','order_delivered_carrier_date'),
                                    ('delivered','order_delivered_customer_date'))}
        chrono_results[layer] = con.execute(sql('05_order_chronology.sql',layer=layer,
                                                **kwargs)).fetchone()
    for i, name in enumerate(chronological, start=1):
        add('temporal', f'TIME.{name}', 'orders', name,
            int(chrono_results['raw'][i]), int(chrono_results['clean'][i]),
            int(chrono_results['raw'][0]), int(chrono_results['clean'][0]),
            'Comparación de dos timestamps presentes y convertibles',
            'R05 puede suprimir únicamente el timestamp transportista en CLEAN')

    for child, child_col, parent, parent_col, note in RELATIONSHIPS:
        if parent == 'category_translation':
            continue  # Opcional: no mezclar traducciones con relaciones obligatorias.
        totals = []
        for layer in ('raw', 'clean'):
            statement = sql('04_orphan_relationship.sql', layer=layer, parent=qi(parent),
                            child=qi(child), parent_key=qi(parent_col),
                            child_key=qi(child_col),
                            missing_child=missing_expr(child_col,layer, alias='c.'))
            pop, errors = con.execute(statement).fetchone()
            totals.append((int(errors),int(pop)))
        add('referential', f'FK.{child}.{child_col}', child, child_col,
            totals[0][0], totals[1][0], totals[0][1], totals[1][1],
            f'Referencia obligatoria a {parent}.{parent_col}; NULL/vacío también falla',note)
    return output, table_counts


def summarize_dimensions(rows: list[dict]) -> list[dict]:
    """Sumar SOLO métricas con la misma unidad dentro de una dimensión."""
    grouped = defaultdict(lambda: [0,0,0,0,0])
    for r in rows:
        agg = grouped[r['dimension']]
        for i,k in enumerate(('before_issues','after_issues','universe_before','universe_after')):
            agg[i] += int(r[k])
        agg[4] += 1
    return [dict(dimension=dim, label=DIMENSIONS[dim][0], unit=DIMENSIONS[dim][2],
                 before_issues=grouped[dim][0], after_issues=grouped[dim][1],
                 universe_before=grouped[dim][2], universe_after=grouped[dim][3],
                 rate_before_pct=safe_rate(grouped[dim][0], grouped[dim][2]),
                 rate_after_pct=safe_rate(grouped[dim][1], grouped[dim][3]),
                 controls=grouped[dim][4])
            for dim in DIMENSIONS]


def audit_rules_and_validation(con, manifest: dict) -> tuple[list[dict], list[dict], dict]:
    run = manifest['transform_run_id']
    catalog = con.execute('''SELECT rule_id, name, severity, policy
        FROM audit.quality_rule_catalog ORDER BY rule_id''').fetchall()
    if len(catalog) != 15:
        raise ValueError('El catálogo de 15 reglas no es completo')
    rule_results = con.execute('''SELECT rule_id, COALESCE(SUM(detected),0),
           COALESCE(SUM(corrected),0), COALESCE(SUM(quarantined),0),
           COALESCE(SUM(unresolved),0),
           COUNT(*) FILTER (WHERE status='FAIL')
           FROM audit.rule_results WHERE run_id=? GROUP BY rule_id''',[run]).fetchall()
    by_code = {r[0]:r for r in rule_results}
    rules = []
    for code,name,severity,policy in catalog:
        if code not in by_code:
            raise ValueError(f'Regla sin resultados en auditoría: {code}')
        result = by_code[code]
        if result[5]:
            raise ValueError(f'La regla {code} tiene un resultado FAIL')
        rules.append({'rule_id':code,'name':name,'severity':severity,'policy':policy,
                      'detected':int(result[1]),'corrected':int(result[2]),
                      'quarantined':int(result[3]),'unresolved':int(result[4])})
    result_rows = con.execute('''SELECT check_id,rule_id,table_name,severity,status,
                             violations,population,explanation
         FROM audit.validation_results WHERE validation_run_id=? ORDER BY check_id''',
         [manifest['validation_run_id']]).fetchall()
    if not result_rows:
        raise ValueError('No existe el resultado de validación del release en auditoría')
    checks = [dict(zip(('check_id','rule_id','table_name','severity','status',
                        'violations','population','explanation'), row)) for row in result_rows]
    failures = [row for row in checks if row['severity']=='CRITICAL' and row['status']=='FAIL']
    if failures:
        raise ValueError(f'Persisten controles críticos en validación del release: {failures}')
    warn = [row for row in checks if row['status']=='WARNING']
    q = count(con, 'SELECT COUNT(*) FROM audit.quarantine_records WHERE run_id=?', [run])
    history = {'quarantined_records':q,'checks_total':len(checks),
               'warning_checks':len(warn),'critical_fails':len(failures)}
    return rules, checks, history


def collect_temporal_audit(con, transform_run_id: str) -> dict:
    events = con.execute('''SELECT issue_code, COUNT(*) AS events,
                      COUNT(DISTINCT order_id) AS affected_orders
         FROM audit.temporal_policy_events WHERE transform_run_id=?
         GROUP BY issue_code ORDER BY issue_code''',[transform_run_id]).fetchall()
    n_orders = count(con, '''SELECT COUNT(DISTINCT order_id)
         FROM audit.temporal_policy_events WHERE transform_run_id=?''',[transform_run_id])
    suppressed = count(con, '''SELECT COUNT(*) FROM clean.orders_temporal_quality
         WHERE carrier_timestamp_quality_status='QUALITY_SUPPRESSED' ''')
    source_missing = count(con, '''SELECT COUNT(*) FROM clean.orders_temporal_quality
         WHERE carrier_timestamp_quality_status='SOURCE_MISSING' ''')
    usable = count(con, '''SELECT COUNT(*) FROM clean.orders_temporal_quality
         WHERE carrier_timestamp_quality_status='USABLE' ''')
    return {'policy_id': POLICY_ID, 'events':sum(int(r[1]) for r in events),
            'orders_affected':n_orders, 'events_by_type':[
                {'issue_code':r[0],'events':int(r[1]), 'orders_affected':int(r[2])} for r in events],
            'carrier_timestamp_suppressed':suppressed,'carrier_timestamp_missing_in_source':source_missing,
            'carrier_timestamp_usable':usable,
            'source_investigation_status':'PENDING_EXTERNAL_VERIFICATION'}


def dictionary_description(table: str, column: str) -> str:
    descriptions = {
        'order_id': 'Identificador del pedido',
        'customer_id': 'Identificador del cliente asociado al pedido',
        'customer_unique_id': 'Identificador histórico del comprador; no equivale a customer_id',
        'order_status': 'Estado del pedido',
        'order_purchase_timestamp': 'Fecha y hora de compra',
        'order_approved_at': 'Fecha y hora de aprobación de pedido',
        'order_delivered_carrier_date': 'Fecha de entrega al transportista, sujeta a R05',
        'order_delivered_customer_date': 'Fecha de entrega al comprador',
        'order_estimated_delivery_date': 'Fecha estimada de entrega',
        'order_item_id': 'Número de artículo dentro del pedido',
        'product_id': 'Identificador del producto',
        'seller_id': 'Identificador del vendedor',
        'shipping_limit_date': 'Fecha límite de envío',
        'price': 'Precio del artículo',
        'freight_value': 'Coste de envío del artículo',
        'payment_sequential': 'Secuencia de pago de un pedido',
        'payment_type': 'Método de pago',
        'payment_installments': 'Número de cuotas de pago',
        'payment_value': 'Importe de la operación de pago',
        'review_id': 'Identificador de reseña (puede repetirse)',
        'review_score': 'Puntuación de reseña',
        'review_comment_title': 'Título opcional de reseña',
        'review_comment_message': 'Mensaje opcional de reseña',
        'review_creation_date': 'Fecha de creación de la reseña',
        'review_answer_timestamp': 'Fecha de respuesta a la reseña',
        'product_category_name': 'Categoría original de producto',
        'product_category_name_english': 'Traducción inglesa opcional de categoría',
        'customer_zip_code_prefix': 'Prefijo postal del cliente, cinco dígitos',
        'seller_zip_code_prefix': 'Prefijo postal del vendedor, cinco dígitos',
        'geolocation_zip_code_prefix': 'Prefijo postal de observación geográfica',
        'geolocation_lat': 'Latitud observada',
        'geolocation_lng': 'Longitud observada',
        'geolocation_city': 'Ciudad de observación geográfica',
        'geolocation_state': 'Estado de observación geográfica',
        'customer_city': 'Ciudad del cliente',
        'customer_state': 'Estado del cliente',
        'seller_city': 'Ciudad del vendedor',
        'seller_state': 'Estado del vendedor',
    }
    if column in descriptions:
        return descriptions[column]
    if column.startswith('product_'):
        return 'Atributo numérico/descriptivo del producto según definición Olist'
    return f'Campo {column} de la tabla {table}; consultar fuente Olist para semántica detallada'


def build_data_dictionary(con, table_counts: list[dict]) -> list[dict]:
    rows = []
    populations = {r['table']:r for r in table_counts}
    for table, fields in SCHEMA.items():
        raw_schema = _get_tabledescribe(con, 'raw', table)
        clean_schema = _get_tabledescribe(con, 'clean', table)
        if set(raw_schema) != set(fields) or list(clean_schema) != fields:
            raise ValueError(f'Cambió el contrato de columnas de {table}')
        for col in fields:
            raw_missing = con.execute(sql('01_field_missing.sql', layer='raw',table=qi(table),
                                          missing_predicate=missing_expr(col,'raw'))).fetchone()[1]
            clean_missing = con.execute(sql('01_field_missing.sql', layer='clean',table=qi(table),
                                            missing_predicate=missing_expr(col,'clean'))).fetchone()[1]
            rel = [f'{parent}.{parent_col}' for child,child_col,parent,parent_col,note in RELATIONSHIPS
                   if child==table and child_col==col]
            flags = []
            if col in KEYS.get(table,()): flags.append('KEY_PART')
            if col in REQUIRED.get(table,()): flags.append('REQUIRED')
            if (table,col) in TIMESTAMPS: flags.append('TIMESTAMP')
            if (table,col) in MONEYS: flags.append('MONETARY')
            if table=='orders' and col=='order_delivered_carrier_date': flags.append('R05_QUALITY_SUPPRESSION')
            rows.append({'table':table, 'field':col, 'description':dictionary_description(table,col),
                         'grain':BY_NAME[table].grain,'raw_type':raw_schema[col],
                         'clean_type':clean_schema[col],
                         'raw_missing':int(raw_missing),'clean_missing':int(clean_missing),
                         'raw_rows':populations[table]['raw_rows'],
                         'clean_rows':populations[table]['clean_rows'],
                         'required':col in REQUIRED.get(table,()),
                         'related_to':'; '.join(rel), 'quality_labels':'; '.join(flags)})
    return rows


def deep_compare_parquets(con, folder: Path) -> list[dict]:
    """Prueba opcional de igualdad multiconjunto CLEAN vs Parquet (incluye duplicados)."""
    output = []
    for table in SCHEMA:
        file = (folder / f'{table}.parquet').resolve().as_posix().replace("'", "''")
        query = f'''SELECT
            (SELECT COUNT(*) FROM (SELECT * FROM clean.{qi(table)}
                                    EXCEPT ALL SELECT * FROM read_parquet('{file}'))) +
            (SELECT COUNT(*) FROM (SELECT * FROM read_parquet('{file}')
                                    EXCEPT ALL SELECT * FROM clean.{qi(table)}))'''
        mismatches = count(con,query)
        if mismatches:
            raise ValueError(f'El contenido del Parquet {table} difiere de CLEAN: {mismatches} filas')
        output.append({'table':table,'multiset_differences':mismatches})
    return output


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8-sig',newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for row in rows:
            writer.writerow({field:row.get(field) if row.get(field) is not None else '' for field in fields})


def markdown_table(headers, rows):
    headers = [str(v) for v in headers]
    def clean(value):
        return str(value).replace('|','\\|').replace('\n',' ')
    return '\n'.join(['| ' + ' | '.join(headers) + ' |',
                      '| ' + ' | '.join('---' for _ in headers) + ' |'] +
                     ['| ' + ' | '.join(clean(v) for v in row) + ' |' for row in rows])


def write_final_report(summary: dict, report_dir: Path) -> Path:
    dimensions=summary['dimensions']
    rows=summary['tables']
    rules=summary['rules']
    temporal=summary['temporal_policy']
    quality=summary['quality_checks']
    lines=[
        '# Fase 8 — Evaluación final de calidad y cierre del pipeline Olist','',
        f"- Fecha UTC: `{summary['evaluated_at_utc']}`",
        f"- Release ID: `{summary['release_id']}`",
        f"- Transformación: `{summary['transform_run_id']}`",
        f"- Validación: `{summary['validation_run_id']}`",
        f"- Estado de validación original: **{summary['validation_status']}**",
        f"- Publicación: **{summary['publication_status']}** | Verificación: **{summary['verification_status']}**",
        f"- Dictamen de Fase 8: **{summary['evaluation_status']}**",'',
        '## 1. Objetivo y evidencia','',
        'Se evalúa el pipeline CSV originales → Python + DuckDB → RAW → 15 reglas SQL → '
        'CLEAN → pruebas críticas → nueve Parquet verificados y reporte de calidad. '
        'RAW y CLEAN son las dos capas de datos persistentes; AUDIT es exclusivamente de control.',
        '',
        'Las cifras siguientes se calculan directamente en esta ejecución sobre RAW/CLEAN '
        'de DuckDB. La publicación se contrasta con los nueve archivos Parquet y SHA-256. '
        'Esto no acredita exactitud frente a una fuente externa.', '',
        '## 2. Comparación de dimensiones de calidad','',
        'Las tasas **no son intercambiables entre dimensiones**: el denominador '
        'puede ser celdas obligatorias, filas con claves, oportunidades de relación, '
        'campos monetarios o comparaciones de eventos temporales. Las sumas dentro de '
        'una dimensión pueden señalar incidencias repetidas en una misma fila.','',
        markdown_table(['Dimensión','Antes','Después','Tasa antes','Tasa después','Unidad'],[
            (r['label'],r['before_issues'],r['after_issues'],
             str(r['rate_before_pct'])+'%' if r['rate_before_pct'] is not None else 'N/D',
             str(r['rate_after_pct'])+'%' if r['rate_after_pct'] is not None else 'N/D',r['unit'])
            for r in dimensions]),'',
        'Para interpretar un indicador, consultar `phase8_quality_metrics.csv`: define '
        'la condición, tabla, campo, numerador, denominador y limitaciones. '
        'Menos incidencias no prueba necesariamente que cada dato haya sido corregido: '
        'puede haberse enviado a cuarentena o declarado no verificable.','',
        '## 3. Conservación de registros','',
        markdown_table(['Tabla','RAW','CLEAN','Diferencia RAW-CLEAN'],[
            (r['table'],r['raw_rows'],r['clean_rows'],r['difference']) for r in rows]),'',
        f"- Total RAW: **{sum(r['raw_rows'] for r in rows):,}** observaciones entre nueve tablas.",
        f"- Total CLEAN: **{sum(r['clean_rows'] for r in rows):,}** observaciones entre nueve tablas.",
        f"- Filas en cuarentena de la transformación: **{quality['quarantined_records']}**.",
        'Los conteos entre tablas no representan personas ni pedidos únicos. '
        'Reseñas y geolocalización mantienen su granularidad legítima.','',
        '## 4. Reglas R01–R15 y decisiones técnicas','',
        'Los recuentos detectados/corregidos pueden solaparse entre reglas y tablas. '
        'No se deben sumar como número de filas únicas.','',
        markdown_table(['Regla','Nombre','Detectadas','Corregidas','Cuarentena','Pendientes'],[
            (r['rule_id'],r['name'],r['detected'],r['corrected'],r['quarantined'],
             r['unresolved']) for r in rules]),'',
        'El detalle de políticas/severidades está en `phase8_rule_catalog.csv`. '
        'La ausencia de correción en una anomalía documentada no es necesariamente un fallo.','',
        '## 5. Regla temporal R05 y restricciones analíticas','',
        f"- Política aplicada: `{temporal['policy_id']}`.",
        f"- Eventos originalmente contradictorios detectados: **{temporal['events']}**.",
        f"- Pedidos afectados (únicos): **{temporal['orders_affected']}**.",
        f"- Timestamps del transportista suprimidos en CLEAN: **{temporal['carrier_timestamp_suppressed']}**.",
        f"- Timestamps que ya faltaban por origen: **{temporal['carrier_timestamp_missing_in_source']}**.",
        f"- Timestamps de transportista utilizables: **{temporal['carrier_timestamp_usable']}**.",
        'Las fechas originales se conservan en RAW/AUDIT; en CLEAN se usa NULL '
        'solo cuando la fecha de transportista participa en cronología imposible. '
        'No se atribuye la causa a un sistema sin evidencia. '
        'El desglose está en `phase8_temporal_events.csv` y la vista '
        '`clean.orders_temporal_quality` distingue `QUALITY_SUPPRESSED`, '
        '`SOURCE_MISSING` y `USABLE`. La investigación del dato de origen sigue pendiente.','',
        '## 6. Pruebas formales y excepciones','',
        f"- Pruebas registradas en la validación publicada: **{quality['checks_total']}**.",
        f"- Pruebas con advertencia: **{quality['warning_checks']}**.",
        f"- Pruebas críticas fallidas: **{quality['critical_fails']}**.",
        'Las advertencias se describen a continuación y no se convierten en '
        'correcciones supuestas:','',
    ]
    warnings=[r for r in summary['checks'] if r['status']=='WARNING']
    lines += [f"- **{r['check_id']}**: {r['violations']} incidencias. {r['explanation']}"
              for r in warnings] or ['- Ninguna advertencia activa.']
    lines += ['', '## 7. Publicación, verificación e integridad','',
              f"- Release publicado: `{summary['release_id']}`.",
              f"- Parquet verificados: **{summary['parquet_files']}**.",
              '- Para cada Parquet se verifican hash SHA-256, tamaño, columnas, tipos '
              'y cantidades; el CSV de excepciones R05 se verifica también.',
              '- La verificación documental ordinaria no demuestra igualdad '
              'byte a byte de los valores entre CLEAN y Parquet: '
              + ('se realizó también comparación multiconjunto completa con EXCEPT ALL.'
                 if summary['deep_comparison_performed'] else
                 'ejecutar `--deep-compare` para una comprobación adicional de todas las filas.'),
              '- La comprobación que antecede a este informe exige que el release '
              'provenga de la última transformación terminada.', '',
              '## 8. Limitaciones y riesgos residuales','',
              '- Exactitud: no verificable sin sistemas originales de pedidos, transportistas y pagos.',
              '- Los eventos temporales R05 conservan investigación de origen pendiente.',
              '- Las excepciones monetarias y de pagos necesitan confirmación de negocio.',
              '- Los valores atípicos IQR son señales estadísticas, no pruebas de error.',
              '- Un campo NULL tratado por política no equivale a una fecha corregida.',
              '- El manifiesto prueba integridad de archivo por hash, no procedencia externa del dato.',
              '- Los reportes auxiliares y auditorías son históricos; no sumarlos entre ejecuciones.',
              '- La publicación en filesystem Windows dispone de rollback, pero no es '
              'una transacción atómica de varios recursos frente a fallos eléctricos.',
              '- Los tests de pytest no se invocan desde este comando; ejecutar '
              '`python -m pytest -q tests` y adjuntar su salida para el cierre.', '',
              '## 9. Mejoras recomendadas','',
              '1. Contrastar anomalías R05 con eventos reales de logística, con responsable y evidencia.',
              '2. Introducir contratos y chequeos de variación (data drift) por ejecución/archivo.',
              '3. Usar versionado externo de snapshots RAW, hashes por CSV y almacenamiento de releases inmutable.',
              '4. Añadir monitoreo/alertas y un scheduler CI/CD, sin aumentar capas persistentes.',
              '5. Diseñar vistas analíticas (opcionalmente estrella) SOBRE CLEAN sin sacrificar granularidad RAW.',
              '6. Medir costes/tiempos y compararlos en ejecuciones sucesivas.', '',
              '## 10. Decisión final y evidencia','',
              '**CIERRE TÉCNICO CON ADVERTENCIAS**: publicadas las nueve tablas y '
              'verificada su integridad, con controles críticos del release conformes. '
              'No se declara exactitud externa ni resolución de las investigaciones R05. '
              'Este dictamen se refiere a la publicación registrada y al snapshot '
              'RAW/CLEAN que fue contrastado en la fecha indicada.', '',
              'Anexos: `phase8_quality_metrics.csv`, `phase8_dimension_summary.csv`, '
              '`phase8_rule_catalog.csv`, `phase8_validation_checks.csv`, '
              '`phase8_temporal_events.csv`, `phase8_data_dictionary.csv` y '
              '`phase8_summary.json`.', '']
    path=report_dir/'phase8_final_report.md'
    path.write_text('\n'.join(lines),encoding='utf-8')
    return path


def create_figure(dimensions: list[dict], folder: Path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    folder.mkdir(parents=True, exist_ok=True)
    labels=[r['label'].split(' (')[0] for r in dimensions]
    before=[r['rate_before_pct'] or 0 for r in dimensions]
    after=[r['rate_after_pct'] or 0 for r in dimensions]
    fig,ax=plt.subplots(figsize=(11,5.6))
    positions=list(range(len(dimensions)))
    ax.barh([x-0.18 for x in positions],before,height=0.35,label='RAW (antes)')
    ax.barh([x+0.18 for x in positions],after,height=0.35,label='CLEAN (después)')
    ax.set_yticks(positions,labels)
    ax.invert_yaxis()
    ax.set_xlabel('Incidencias por cada 100 oportunidades de control (%)')
    ax.set_title('Fase 8 — Indicadores de calidad RAW vs CLEAN')
    ax.grid(axis='x',alpha=0.2)
    ax.legend()
    fig.tight_layout()
    path=folder/'phase8_before_after_quality.png'
    fig.savefig(path,dpi=160)
    plt.close(fig)
    return path


def evaluate_project(*, db_path=DB_PATH, parquet_dir=DATA_CLEAN_DIR,
                     report_dir=QUALITY_REPORTS_DIR, dictionary_dir=DICTIONARY_DIR,
                     figures_dir=FIGURES_DIR, create_figures=True,
                     deep_compare=False) -> dict:
    """Ejecución únicamente de lectura de datos + escritura de informes Fase 8."""
    import duckdb
    db_path=Path(db_path)
    parquet_dir=Path(parquet_dir)
    report_dir=Path(report_dir)
    dictionary_dir=Path(dictionary_dir)
    figures_dir=Path(figures_dir)
    if not db_path.is_file():
        raise FileNotFoundError(f'Falta la base de datos: {db_path}')
    manifest_path=parquet_dir/RELEASE_FILE
    if not manifest_path.is_file():
        raise FileNotFoundError('No existe release_manifest.json; ejecutar Fase 7 antes de Fase 8')
    # No aceptar un resumen viejo: volver a abrir y verificar físicamente los Parquet.
    verification=verify_release(parquet_dir,report_dir)
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest['release_id'] != verification['release_id']:
        raise ValueError('La verificación no corresponde al mismo release')
    with duckdb.connect(str(db_path),read_only=True) as con:
        release_state=assert_release_snapshot(con,manifest)
        metrics,table_counts=calculate_metrics(con)
        dimension_summary=summarize_dimensions(metrics)
        rules,checks,history=audit_rules_and_validation(con,manifest)
        temporal=collect_temporal_audit(con,manifest['transform_run_id'])
        if temporal['events'] != int(verification['temporal_policy_events']):
            raise ValueError('Los eventos R05 no coinciden con el CSV publicado')
        if temporal['orders_affected'] != temporal['carrier_timestamp_suppressed']:
            raise ValueError('La vista temporal no coincide con las supresiones registradas')
        dictionary=build_data_dictionary(con,table_counts)
        deep=deep_compare_parquets(con,parquet_dir) if deep_compare else []
    summary={
        'evaluated_at_utc':datetime.now(timezone.utc).isoformat(),
        'evaluation_status':'EVALUATED_WITH_WARNINGS' if history['warning_checks'] else 'EVALUATED',
        'release_id':manifest['release_id'],
        'transform_run_id':manifest['transform_run_id'],
        'validation_run_id':manifest['validation_run_id'],
        'validation_status':release_state['validation_status'],
        'publication_status':manifest['status'],
        'verification_status':verification['status'],
        'parquet_files':verification['parquet_tables'],
        'deep_comparison_performed':bool(deep_compare),
        'deep_comparison':deep,
        'tables':table_counts,'dimensions':dimension_summary,
        'temporal_policy':temporal,'quality_checks':history,
        'rules':rules,'checks':checks,
        'data_dictionary_fields':len(dictionary),
        'report_path':str(report_dir/'phase8_final_report.md'),
        'dictionary_path':str(dictionary_dir/'phase8_data_dictionary.csv'),
        'figure_path':str(figures_dir/'phase8_before_after_quality.png') if create_figures else None,
        'methodology': ('Las tasas de cada dimensión usan denominadores explícitos; '
                        'no significan exactitud externa. Métricas calculadas sobre RAW/CLEAN real.')
    }
    report_dir.mkdir(parents=True,exist_ok=True)
    dictionary_dir.mkdir(parents=True,exist_ok=True)
    write_csv(report_dir/'phase8_quality_metrics.csv',metrics,list(metrics[0]))
    write_csv(report_dir/'phase8_dimension_summary.csv',dimension_summary,list(dimension_summary[0]))
    write_csv(report_dir/'phase8_rule_catalog.csv',rules,list(rules[0]))
    write_csv(report_dir/'phase8_validation_checks.csv',checks,list(checks[0]))
    event_rows=temporal['events_by_type']
    write_csv(report_dir/'phase8_temporal_events.csv',event_rows,
              ['issue_code','events','orders_affected'])
    write_csv(dictionary_dir/'phase8_data_dictionary.csv',dictionary,list(dictionary[0]))
    report=write_final_report(summary,report_dir)
    if create_figures:
        create_figure(dimension_summary,figures_dir)
    summary['report']=str(report)
    # Marcador final: emitir summary sólo tras completar todos los entregables.
    (report_dir/'phase8_summary.json').write_text(
        json.dumps(summary,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    return summary


def main():
    parser=argparse.ArgumentParser(description='Fase 8: evaluación final, sin modificar RAW/CLEAN ni publicar de nuevo')
    parser.add_argument('--db',type=Path,default=DB_PATH)
    parser.add_argument('--parquet',type=Path,default=DATA_CLEAN_DIR)
    parser.add_argument('--reports',type=Path,default=QUALITY_REPORTS_DIR)
    parser.add_argument('--dictionary',type=Path,default=DICTIONARY_DIR)
    parser.add_argument('--figures',type=Path,default=FIGURES_DIR)
    parser.add_argument('--no-figures',action='store_true')
    parser.add_argument('--deep-compare',action='store_true',
                        help='Comparar todas las filas Parquet vs CLEAN como multiconjunto; consume más CPU/RAM')
    args=parser.parse_args()
    try:
        outcome=evaluate_project(db_path=args.db,parquet_dir=args.parquet,
                                 report_dir=args.reports,dictionary_dir=args.dictionary,
                                 figures_dir=args.figures,create_figures=not args.no_figures,
                                 deep_compare=args.deep_compare)
    except Exception as exc:
        print(f'[FASE 8][ERROR] {exc}')
        raise SystemExit(1) from exc
    print(f"[FASE 8] {outcome['evaluation_status']}")
    print(f"[PUBLICACIÓN] {outcome['publication_status']} / {outcome['verification_status']}")
    print(f"[PARQUET] {outcome['parquet_files']} archivos")
    print(f"[R05] {outcome['temporal_policy']['events']} eventos de origen documentados")
    print(f"[INFORME] {outcome['report']}")


if __name__=='__main__':
    main()
