"""Fase 3: diagnóstico integral, SOLO LECTURA, de las nueve tablas RAW.

Ejecutar desde la raíz del proyecto: python -m src.diagnose
- No modifica CSV, RAW, CLEAN ni audit.
- Los resultados se calculan sobre la base persistente cargada por Fase 2.
- SQL analítico en sql/profiling; Python orquesta y escribe reportes.
- Las 'anomalías' se clasifican para revisión: aún no se limpian los datos.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config import DB_PATH, QUALITY_REPORTS_DIR, FIGURES_DIR, ROOT_DIR
from .extract import qi
from .model_metadata import (TABLE_SPECS, RELATIONSHIPS, TIMESTAMP_CANDIDATES,
                             MONETARY_CANDIDATES, CATEGORY_CANDIDATES)
from .understand import _key_metrics, _fk_metrics, _null_and_empty

SQL_DIR = ROOT_DIR / 'sql' / 'profiling'

NUMERIC_CANDIDATES = {
    'order_items': ('order_item_id',),
    'order_payments': ('payment_sequential', 'payment_installments'),
    'order_reviews': ('review_score',),
    'products': ('product_name_lenght', 'product_description_lenght',
                 'product_photos_qty', 'product_weight_g', 'product_length_cm',
                 'product_height_cm', 'product_width_cm'),
}
EXTRA_CATEGORY_CANDIDATES = {
    'customers': ('customer_state', 'customer_city'),
    'sellers': ('seller_state', 'seller_city'),
    'geolocation': ('geolocation_state', 'geolocation_city'),
    'products': ('product_category_name',),
    'order_reviews': ('review_score',),
}
POSTAL_CANDIDATES = {
    'customers': 'customer_zip_code_prefix',
    'sellers': 'seller_zip_code_prefix',
    'geolocation': 'geolocation_zip_code_prefix',
}


def sql_from_file(filename: str, **identifiers: str) -> str:
    """Interpolación restringida: los identificadores ya vienen de DESCRIBE RAW.

    Usar parámetros en SQL cuando se interpolan *valores*; aquí sólo se cita
    el nombre de tabla/columna verificado y el propio archivo SQL es local.
    """
    raw = (SQL_DIR / filename).read_text(encoding='utf-8')
    return raw.format(**{name: qi(value) for name, value in identifiers.items()}).strip().rstrip(';')


def to_plain(value):
    from decimal import Decimal
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime,)):
        return value.isoformat(sep=' ')
    return value


def query_dicts(con, sql: str, params=None) -> list[dict]:
    cursor = con.execute(sql, params or [])
    names = [item[0] for item in cursor.description]
    return [{name: to_plain(cell) for name, cell in zip(names, row)}
            for row in cursor.fetchall()]


def output_csv(folder: Path, basename: str, records: list[dict], columns: list[str]):
    path = folder / f'phase3_{basename}.csv'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        for record in records:
            writer.writerow({k: '' if record.get(k) is None else record.get(k) for k in columns})
    return path


def check_columns(actual: dict[str, str], wanted: tuple[str, ...] | list[str]):
    return all(column in actual for column in wanted)


def raw_columns(con, table: str) -> dict[str, str]:
    return {name: dtype for name, dtype, *_ in
            con.execute(f'DESCRIBE raw.{qi(table)}').fetchall()}


def count_row(con, query: str):
    return con.execute(query).fetchone()[0]


def percent(numerator, denominator):
    return round(100 * (numerator or 0) / denominator, 4) if denominator else 0.0


def _reference_issues(con, columns: dict[str, dict[str, str]]):
    result = []
    for child, child_col, parent, parent_col, note in RELATIONSHIPS:
        row = _fk_metrics(con, child, child_col, parent, parent_col, columns, note)
        row['severity'] = 'INFORMATIVO' if parent == 'category_translation' else 'ALTO'
        row['child_records'] = count_row(con, f'SELECT COUNT(*) FROM raw.{qi(child)}')
        row['orphan_pct'] = (percent(row['orphan_rows'], row['child_records'])
                              if row['orphan_rows'] is not None else None)
        result.append(row)
    return result


def _cast_checks(con, columns: dict[str, dict[str, str]]):
    rows = []
    expected = []
    for table, names in TIMESTAMP_CANDIDATES.items():
        expected.extend((table, field, 'TIMESTAMP') for field in names)
    for table, names in MONETARY_CANDIDATES.items():
        expected.extend((table, field, 'DECIMAL(18,2)') for field in names)
    for table, names in NUMERIC_CANDIDATES.items():
        expected.extend((table, field, 'BIGINT') for field in names)
    for table, field, target_type in expected:
        if field not in columns[table]:
            rows.append(dict(table=table, column=field, expected_type=target_type,
                             nonempty_values=None, conversion_failures=None,
                             fail_pct=None, status='MISSING_COLUMN'))
            continue
        quoted = qi(field)
        source = f'NULLIF(TRIM({quoted}), \'\')'
        query = f'''
            SELECT COUNT(*) FILTER (WHERE {source} IS NOT NULL) AS present,
                   COUNT(*) FILTER (WHERE {source} IS NOT NULL
                                   AND TRY_CAST({source} AS {target_type}) IS NULL) AS errors
            FROM raw.{qi(table)}
        '''
        present, errors = con.execute(query).fetchone()
        rows.append(dict(table=table, column=field, expected_type=target_type,
                         nonempty_values=present, conversion_failures=errors,
                         fail_pct=percent(errors, present),
                         status='REVIEW' if errors else 'NO_CONVERSION_FAILURES'))
    return rows


def _id_cardinality(con, columns: dict[str, dict[str, str]]):
    result = []
    for spec in TABLE_SPECS:
        table = spec.name
        for col in columns[table]:
            if (col.endswith('_id') or col.endswith('_unique_id') or
                col.endswith('_prefix') or col in ('order_item_id','payment_sequential')):
                q = qi(col)
                total, nonempty, distinct_count = con.execute(f'''
                    SELECT COUNT(*),
                           COUNT(NULLIF(TRIM({q}),'')),
                           COUNT(DISTINCT NULLIF(TRIM({q}),''))
                    FROM raw.{qi(table)}
                ''').fetchone()
                result.append(dict(table=table, column=col,
                    total_rows=total, nonempty_values=nonempty,
                    distinct_nonempty_values=distinct_count,
                    repeated_nonempty_rows=nonempty-distinct_count,
                    distinct_ratio_pct=percent(distinct_count, nonempty),
                    interpretation='Cardinalidad descriptiva, NO implica unicidad obligatoria'))
    return result


def _duplicate_checks(con, cols: dict[str, dict[str, str]]):
    exact, keys = [], []
    for spec in TABLE_SPECS:
        total, distinct = con.execute(sql_from_file(
            '02_exact_duplicates.sql', table=spec.name)).fetchone()
        exact.append(dict(table=spec.name, total_rows=total, distinct_rows=distinct,
                          exact_duplicate_excess=total-distinct,
                          exact_duplicate_pct=percent(total-distinct,total),
                          note=('Observaciones geográficas repetidas pueden ser legítimas'
                                if spec.name=='geolocation' else 'Requiere revisar la granularidad')))
        if spec.candidate_key:
            keys.append(_key_metrics(con, spec.name, spec.candidate_key, cols[spec.name]))
    for fields in [('review_id',), ('review_id','order_id')]:
        res = _key_metrics(con, 'order_reviews', fields, cols['order_reviews'])
        res['status'] = 'DIAGNOSTIC_ONLY_' + res['status']
        keys.append(res)
    return exact, keys


def _date_ranges(con, columns: dict[str, dict[str,str]]):
    rows = []
    for table, fields in TIMESTAMP_CANDIDATES.items():
        for field in fields:
            if field not in columns[table]:
                continue
            casted = f'TRY_CAST(NULLIF(TRIM({qi(field)}),\'\') AS TIMESTAMP)'
            minimum, maximum, present = con.execute(f'''
                SELECT MIN({casted}), MAX({casted}),
                       COUNT(NULLIF(TRIM({qi(field)}),''))
                FROM raw.{qi(table)}
            ''').fetchone()
            rows.append(dict(table=table, column=field,
                             earliest=to_plain(minimum), latest=to_plain(maximum),
                             nonempty_values=present))
    return rows


def _temporal_checks(con, cols):
    required = ('order_id', 'order_status', *TIMESTAMP_CANDIDATES['orders'])
    if not check_columns(cols['orders'], required):
        return [dict(rule='TEMPORAL_QUERIES_NOT_EVALUATED', count=None,
                     severity='WARNING', explanation='Faltan columnas de eventos de pedidos')]
    row = query_dicts(con, sql_from_file('04_order_temporal.sql'))[0]
    severities = {
        'approval_before_purchase': ('ALTO','Aprobación anterior a compra'),
        'carrier_before_purchase': ('ALTO','Despacho anterior a compra'),
        'delivery_before_purchase': ('ALTO','Entrega anterior a compra'),
        'delivery_before_carrier': ('ALTO','Entrega anterior al despacho'),
        'estimated_before_purchase': ('MEDIO','Estimación anterior a la compra: revisar semántica'),
        'delivered_after_estimate': ('INFORMATIVO','Retraso legítimo posible, NO error automático'),
        'delivered_status_without_delivery_date': ('MEDIO','Estado entregado sin fecha real: revisar'),
        'canceled_status_with_delivery_date': ('MEDIO','Cancelado con fecha real: revisar caso'),
    }
    return [dict(rule=key, count=row[key], severity=level, explanation=reason,
                 evaluated_records=row['orders']) for key,(level,reason) in severities.items()]


def _money_checks(con, cols):
    distribution, issues, iqr = [], [], []
    for table, fields in MONETARY_CANDIDATES.items():
        for col in fields:
            if col not in cols[table]:
                continue
            q = qi(col)
            value = f'TRY_CAST(NULLIF(TRIM({q}),\'\') AS DECIMAL(18,2))'
            row = query_dicts(con, f'''
                SELECT COUNT(*) AS total_rows,
                       COUNT({value}) AS valid_numeric_count,
                       MIN({value}) AS minimum,
                       MAX({value}) AS maximum,
                       AVG({value}) AS average,
                       SUM({value}) AS total_value,
                       COUNT(*) FILTER (WHERE {value} < 0) AS negative_count,
                       COUNT(*) FILTER (WHERE {value} = 0) AS zero_count,
                       COUNT(*) FILTER (WHERE NULLIF(TRIM({q}), '') IS NOT NULL
                                            AND {value} IS NULL) AS invalid_count
                FROM raw.{qi(table)}
            ''')[0]
            distribution.append(dict(table=table, column=col, **{k: row[k]
                for k in ('total_rows','valid_numeric_count','minimum','maximum','average','total_value')}))
            issues.append(dict(table=table, column=col, negative_count=row['negative_count'],
                               zero_count=row['zero_count'], invalid_conversion_count=row['invalid_count'],
                               policy='Negativos: revisar; ceros: investigar promociones; no imputar precios'))
            data = query_dicts(con, sql_from_file('03_iqr.sql', table=table, column=col))[0]
            iqr.append(dict(table=table, column=col, **data,
                            outlier_pct=percent(data['outlier_count'], data['valid_count']),
                            treatment='Detectar y contextualizar; NO eliminar automáticamente'))
    return distribution, issues, iqr


def _categories(con, cols):
    seen = []
    categories = dict(CATEGORY_CANDIDATES)
    for table, fields in EXTRA_CATEGORY_CANDIDATES.items():
        categories[table] = categories.get(table, ()) + fields
    for table, fields in categories.items():
        for field in dict.fromkeys(fields):
            if field not in cols[table]:
                continue
            values = query_dicts(con, f'''
                SELECT {qi(field)} AS value, COUNT(*) AS records_count
                FROM raw.{qi(table)}
                GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 25
            ''')
            seen.extend(dict(table=table, column=field, **entry) for entry in values)
    return seen


def _postals(con, cols):
    results = []
    for table, field in POSTAL_CANDIDATES.items():
        if field not in cols[table]:
            results.append(dict(table=table, column=field, total_rows=None,
                                missing_prefix=None, invalid_format=None,
                                distinct_prefixes=None, status='MISSING_COLUMN'))
            continue
        row = query_dicts(con, sql_from_file('07_postal.sql', table=table, column=field))[0]
        results.append(dict(table=table, column=field, **row,
                            status='REVIEW' if row['invalid_format'] else 'FORMAT_OK',
                            note='Prefijo de cinco dígitos, no código postal completo'))
    return results


def _status_checks(con, cols):
    if 'order_status' not in cols['orders']:
        return []
    return query_dicts(con, sql_from_file('06_status.sql'))


def _payments(con, cols, max_rows: int = 100):
    if not (check_columns(cols['order_items'], ('order_id','price','freight_value')) and
            check_columns(cols['order_payments'], ('order_id','payment_value'))):
        return ([dict(reconciliation_status='NOT_EVALUATED', orders=None,
                      reason='Faltan columnas requeridas')], [])
    query = sql_from_file('05_payment_reconciliation.sql')
    summary = query_dicts(con, f'''
        SELECT reconciliation_status, COUNT(*) AS orders,
               SUM(ABS(difference)) AS absolute_difference_sum,
               MAX(ABS(difference)) AS maximum_absolute_difference
        FROM ({query}) r
        GROUP BY reconciliation_status
        ORDER BY orders DESC, reconciliation_status ASC
    ''')
    details = query_dicts(con, f'''
        SELECT order_id, item_rows, payment_rows, item_price, freight, paid,
               items_plus_freight, difference, invalid_item_rows,
               invalid_payment_rows, reconciliation_status
        FROM ({query}) r
        WHERE reconciliation_status <> 'MATCHED'
        ORDER BY CASE WHEN difference IS NULL THEN 0 ELSE ABS(difference) END DESC,
                 order_id LIMIT {int(max_rows)}
    ''')
    return summary, details


def _summarize_issues(tables: dict) -> list[dict]:
    """Incidencias para priorización, no un conteo de casos únicos ni de errores probados."""
    out = []
    def add(rule, table, count, severity, interpretation):
        if count is not None and int(count) > 0:
            out.append(dict(rule=rule, table=table, affected_rows=int(count),
                            severity=severity, interpretation=interpretation))
    for r in tables['duplicates']:
        add('DUPLICATE_EXACT', r['table'], r['exact_duplicate_excess'], 'MEDIO',
            'Coincidencias exactas: investigar antes de excluir')
    for r in tables['keys']:
        if not r['status'].startswith('DIAGNOSTIC_ONLY'):
            add('DUPLICATE_CANDIDATE_KEY', r['table'], r['excess_duplicate_rows'], 'ALTO',
                'Clave candidata repetida; comprobar si son duplicados de negocio')
            add('MISSING_CANDIDATE_KEY', r['table'], r['missing_key_rows'], 'ALTO',
                'Clave candidata faltante; evaluar tratamiento')
    for r in tables['conversions']:
        add('CONVERSION_FAILURE', r['table'] + '.' + r['column'],
            r['conversion_failures'], 'ALTO', 'Valor no convertible al tipo esperado')
    for r in tables['references']:
        add('ORPHAN_RELATIONSHIP', r['child_table'] + '.' + r['child_column'],
            r['orphan_rows'], r['severity'], r['relationship'])
    for r in tables['monetary_issues']:
        add('NEGATIVE_MONETARY_VALUE', r['table']+'.'+r['column'],
            r['negative_count'],'ALTO','Investigar origen, sin imputar precio')
        add('ZERO_MONETARY_VALUE', r['table']+'.'+r['column'],
            r['zero_count'],'MEDIO','Cero puede ser legítimo; validar en negocio')
    for r in tables['postals']:
        add('INVALID_POSTAL_PREFIX',r['table']+'.'+r['column'],r['invalid_format'],
            'MEDIO','Formato diferente de cinco dígitos')
    for r in tables['temporal']:
        if r['rule'] != 'delivered_after_estimate':
            add(r['rule'], 'orders', r['count'],r['severity'],r['explanation'])
    for r in tables['payment_summary']:
        if r['reconciliation_status'] != 'MATCHED':
            add('PAYMENT_'+r['reconciliation_status'],'orders',r['orders'],
                'MEDIO','Diferencias requieren contexto de descuentos, vouchers y estado')
    return sorted(out, key=lambda x: (-x['affected_rows'], x['rule'], x['table']))


def _kpis(tables: dict):
    """Línea de base con denominadores explícitos, NO puntaje global de calidad."""
    rows=[]
    def push(name, ok, total, detail):
        rows.append(dict(indicator=name, compliant_observations=ok,
                         evaluated_observations=total, compliance_pct=percent(ok,total),
                         denominator_definition=detail))
    # Cada clave candidata cuenta como un conjunto de registros de su tabla.
    keys=[r for r in tables['keys'] if not r['status'].startswith('DIAGNOSTIC_ONLY')
          and r['missing_key_rows'] is not None]
    totals={r['table']:r['records_count'] for r in tables['overview']}
    assessed=sum(totals[r['table']] for r in keys)
    missing=sum(r['missing_key_rows'] for r in keys)
    push('candidate_key_completeness',assessed-missing,assessed,
         'Filas de tablas con clave candidata definida; se excluyen las reseñas')
    types=[r for r in tables['conversions'] if r['nonempty_values'] is not None]
    assessed=sum(r['nonempty_values'] for r in types)
    failed=sum(r['conversion_failures'] for r in types)
    push('type_conversion_validity',assessed-failed,assessed,
         'Valores NO vacíos de columnas evaluadas; una fila aporta varios campos')
    relations=[r for r in tables['references'] if r['severity']!='INFORMATIVO'
               and r['orphan_rows'] is not None]
    assessed=sum(r['child_records'] for r in relations)
    orphan=sum(r['orphan_rows'] for r in relations)
    push('referential_integrity',assessed-orphan,assessed,
         'Filas hijas POR RELACIÓN obligatoria evaluada; una fila puede contarse varias veces')
    postal=[r for r in tables['postals'] if r['total_rows'] is not None]
    assessed=sum(r['total_rows'] for r in postal)
    postal_invalid=sum(r['missing_prefix']+r['invalid_format'] for r in postal)
    push('postal_prefix_format',assessed-postal_invalid,assessed,
         'Prefijos de clientes, vendedores y geolocalización; formato solamente')
    pay={r['reconciliation_status']:r['orders'] for r in tables['payment_summary']
         if r['orders'] is not None}
    assessed=sum(pay.get(k,0) for k in ('MATCHED','REVIEW_DIFFERENCE','INVALID_VALUES','INCOMPLETE_AMOUNTS'))
    push('payment_reconciliation',pay.get('MATCHED',0),assessed,
         'Pedidos con artículos y pagos; sin faltantes de conjuntos; tolerancia 0.01')
    return rows


def _make_figures(tables: dict, fig_dir: Path, db_path: Path):
    """Usa métricas agregadas para evitar cargar geolocation completo a Python."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    missing = sorted([r for r in tables['missingness'] if r['missing_or_empty_pct'] > 0],
                     key=lambda x: x['missing_or_empty_pct'], reverse=True)[:20]
    if missing:
        labels = [r['table']+'.'+r['column'] for r in missing]
        fig, ax = plt.subplots(figsize=(11, max(5, len(labels)*0.32)))
        ax.barh(labels[::-1], [r['missing_or_empty_pct'] for r in missing][::-1])
        ax.set_xlabel('% NULL / vacío / espacios (RAW)')
        ax.set_title('Top 20 columnas con datos ausentes — Fase 3')
        fig.tight_layout()
        path = fig_dir / 'phase3_missingness_top20.png'
        fig.savefig(path, dpi=160); plt.close(fig); saved.append(path.name)
    duplicates = tables['duplicates']
    if duplicates:
        fig, ax = plt.subplots(figsize=(10,4.5))
        ax.bar([x['table'] for x in duplicates], [x['exact_duplicate_excess'] for x in duplicates])
        ax.set_ylabel('Excedentes de duplicados exactos (sin eliminar)')
        ax.tick_params(axis='x', rotation=40)
        ax.set_title('Coincidencias exactas en RAW')
        fig.tight_layout()
        path = fig_dir / 'phase3_exact_duplicates.png'
        fig.savefig(path, dpi=160); plt.close(fig); saved.append(path.name)
    statuses = tables['statuses']
    if statuses:
        fig, ax = plt.subplots(figsize=(9, 4))
        ax.bar([str(x['order_status']) for x in statuses], [x['records_count'] for x in statuses])
        ax.tick_params(axis='x', rotation=35)
        ax.set_ylabel('Cantidad de pedidos')
        ax.set_title('Distribución de estados de pedidos')
        fig.tight_layout()
        path = fig_dir / 'phase3_order_status.png'
        fig.savefig(path, dpi=160); plt.close(fig); saved.append(path.name)
    recon = tables['payment_summary']
    recon = [r for r in recon if r['orders'] is not None]
    if recon:
        fig, ax = plt.subplots(figsize=(10, 4.5))
        ax.bar([r['reconciliation_status'] for r in recon],[r['orders'] for r in recon])
        ax.tick_params(axis='x', rotation=30)
        ax.set_ylabel('Pedidos')
        ax.set_title('Conciliación de pagos: categorías de revisión')
        fig.tight_layout()
        path = fig_dir / 'phase3_payment_reconciliation.png'
        fig.savefig(path, dpi=160); plt.close(fig); saved.append(path.name)
    # Histogramas y diagramas de caja para las tres medidas monetarias.
    # Sólo se llevan a memoria las tres columnas numéricas, nunca geolocation.
    import duckdb
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        for table, column in [('order_items','price'),('order_items','freight_value'),
                              ('order_payments','payment_value')]:
            q = qi(column)
            values = [row[0] for row in con.execute(f"""
                SELECT TRY_CAST(NULLIF(TRIM({q}), '') AS DOUBLE) AS value
                FROM raw.{qi(table)}
                WHERE TRY_CAST(NULLIF(TRIM({q}), '') AS DOUBLE) IS NOT NULL
            """).fetchall()]
            if not values:
                continue
            fig, ax = plt.subplots(figsize=(9,4.5))
            ax.hist(values, bins=50)
            ax.set_xlabel(f'{table}.{column} (valor leído, sin limpieza)')
            ax.set_ylabel('Frecuencia')
            ax.set_title('Distribución monetaria: '+column)
            fig.tight_layout()
            name=f'phase3_hist_{table}_{column}.png'
            fig.savefig(fig_dir/name,dpi=160)
            plt.close(fig); saved.append(name)
            fig, ax = plt.subplots(figsize=(9,2.8))
            ax.boxplot(values,vert=False)
            ax.set_xlabel(f'{table}.{column} (valor leído, sin limpieza)')
            ax.set_title('Diagrama de caja: '+column)
            fig.tight_layout()
            name=f'phase3_box_{table}_{column}.png'
            fig.savefig(fig_dir/name,dpi=160)
            plt.close(fig); saved.append(name)
    finally:
        con.close()
    return saved


def make_markdown(tables: dict, report_dir: Path, figure_names: list[str], metadata: dict):
    totals = {r['table']:r['records_count'] for r in tables['overview']}
    md = [
        '# Reporte inicial de calidad — Fase 3 (antes de ETL)', '',
        f"Generado (UTC): {metadata['generated_at_utc']}. ID: `{metadata['run_id']}`.", '',
        '**Fuente:** `raw` en `database/ecommerce.duckdb`. **Política:** consultas de solo lectura; '
        'no se han limpiado registros ni construido CLEAN.', '',
        '## 1. Alcance y volumen', '',
        '| Tabla RAW | Filas | Columnas | Duplicados exactos excedentes |',
        '|---|---:|---:|---:|',
    ]
    by_dup = {r['table']:r for r in tables['duplicates']}
    for item in tables['overview']:
        md.append(f"| {item['table']} | {item['records_count']:,} | {item['columns_count']} | {by_dup[item['table']]['exact_duplicate_excess']:,} |")
    md += ['', 'No tratar automáticamente como duplicados los artículos, pagos, reseñas o observaciones geográficas repetidos.',
           '', '## 2. Incidencias identificadas y priorización', '',
           'Los conteos siguientes pueden **solaparse**: NO deben sumarse como número de filas únicas con error.', '',
           '| Regla preliminar | Tabla/campo | Casos | Nivel | Interpretación |',
           '|---|---|---:|---|---|']
    for r in tables['issues'][:100]:
        md.append(f"| {r['rule']} | {r['table']} | {r['affected_rows']:,} | {r['severity']} | {r['interpretation']} |")
    if not tables['issues']:
        md.append('| Sin incidencias cuantificadas por estas comprobaciones | — | 0 | — | No equivale a certificar exactitud |')
    md += ['', '### Indicadores iniciales (no es un puntaje global)', '',
           '| Dimensión | Cumplen | Evaluadas | % | Denominador |', '|---|---:|---:|---:|---|']
    for r in tables['kpis']:
        md.append(f"| {r['indicator']} | {r['compliant_observations']} | {r['evaluated_observations']} | {r['compliance_pct']}% | {r['denominator_definition']} |")
    md += ['', '## 3. Completitud', '',
           'Faltante = `NULL` + cadena vacía + cadena sólo con espacios. '
           'La exigibilidad depende de la columna y el estado del pedido; no todo faltante es incorrecto.', '',
           '| Campo (top 20) | Filas ausentes | % |', '|---|---:|---:|']
    for r in sorted(tables['missingness'],key=lambda r:r['missing_or_empty_pct'],reverse=True)[:20]:
        absent=r['null_count']+r['empty_count']+r['whitespace_only_count']
        md.append(f"| {r['table']}.{r['column']} | {absent:,} | {r['missing_or_empty_pct']:.2f}% |")
    md += ['', '## 4. Claves candidatas e integridad', '',
           'La clave de reseñas se informa sólo a modo diagnóstico. No asumir unicidad de `review_id` o prefijos geográficos.', '',
           '| Tabla | Clave candidata | Falta | Excedentes duplicados | Estado |',
           '|---|---|---:|---:|---|']
    for r in tables['keys']:
        md.append(f"| {r['table']} | {r['candidate_key']} | {r['missing_key_rows']} | {r['excess_duplicate_rows']} | {r['status']} |")
    md += ['', '| Relación hija → padre | Huérfanos | % | Clasificación |', '|---|---:|---:|---|']
    for r in tables['references']:
        md.append(f"| {r['child_table']}.{r['child_column']} → {r['parent_table']}.{r['parent_column']} | "
                  f"{r['orphan_rows']} | {r['orphan_pct']}% | {r['severity']} |")
    md += ['', '## 5. Fechas, importes y valores atípicos', '',
           'Las comparaciones temporales excluyen pares de fechas no convertibles o ausentes; los fallos de '
           'conversión se reportan por separado. Una entrega posterior a la fecha estimada no es un error imposible.', '',
           '| Comprobación temporal | Casos | Interpretación |', '|---|---:|---|']
    for r in tables['temporal']:
        md.append(f"| {r['rule']} | {r['count']} | {r['explanation']} |")
    md += ['', '| Campo | Mínimo | Máximo | Q1 | Q3 | Atípicos IQR | % |',
           '|---|---:|---:|---:|---:|---:|---:|']
    for r in tables['outliers']:
        md.append(f"| {r['table']}.{r['column']} | {r['minimum']} | {r['maximum']} | "
                  f"{r['q1']} | {r['q3']} | {r['outlier_count']} | {r['outlier_pct']}% |")
    md += ['', 'Los valores atípicos (IQR) no se eliminan: deben analizarse por producto, vendedor o contexto.',
           '', '## 6. Conciliación de pagos', '',
           'Se agregan por `order_id` los artículos y fletes **antes** del join con pagos. '
           'Tolerancia preliminar: **0.01** en moneda del dataset; las diferencias son alertas de revisión, '
           'no correcciones automáticas.', '',
           '| Estado | Pedidos | Diferencia absoluta agregada |', '|---|---:|---:|']
    for r in tables['payment_summary']:
        md.append(f"| {r['reconciliation_status']} | {r['orders']} | {r.get('absolute_difference_sum')} |")
    md += ['', 'Los casos `NO_ITEMS` o `NO_PAYMENTS` pueden deberse a estados legítimos; '
           'las diferencias pueden requerir revisar vouchers, descuentos y redondeo.',
           '', '## 7. Prefijos postales y categorías', '',
           'Formato esperado del **prefijo**: cinco dígitos (no confundir con código postal completo). '
           'La validación no demuestra existencia geográfica.', '',
           '| Tabla | Faltantes | Formato inválido | Prefijos distintos |', '|---|---:|---:|---:|']
    for r in tables['postals']:
        md.append(f"| {r['table']} | {r['missing_prefix']} | {r['invalid_format']} | {r['distinct_prefixes']} |")
    md += ['', 'Para distribuciones de categorías, ver `phase3_categories.csv`.',
           '', '## 8. Gráficas generadas', '']
    for file in figure_names:
        md.append(f'![{file}](../figures/{file})')
    md += ['', '## 9. Diccionario, fórmulas y restricciones', '',
           '- **Duplicados exactos excedentes** = total filas − filas distintas en *todas* las columnas.',
           '- **Duplicados por clave candidata** = Σ(max(grupo − 1, 0)) sobre claves completas.',
           '- **Completitud (%)** = 100 × (1 − valores ausentes / filas de tabla); no todo campo es obligatorio.',
           '- **Integridad referencial (%)** = 100 × (1 − huérfanos / filas de tabla hija).',
           '- **IQR** = Q3 − Q1; límites = Q1 − 1.5×IQR y Q3 + 1.5×IQR.',
           '- **Conciliación** = SUM(precio + flete por pedido) − SUM(pagos por pedido).',
           '- **Exactitud:** NO evaluada: se requiere una fuente externa de verdad.', '',
           '**Estado del proyecto:** sólo diagnóstico RAW. Las quince reglas se aplicarán de RAW a CLEAN '
           'en Fase 4; las pruebas formales y la exportación Parquet se ejecutan después.', '',
           'Los porcentajes reportados tienen denominadores específicos; se deben revisar los archivos CSV '
           'completos para tomar decisiones de limpieza. No afirmar que toda alerta sea error.', '']
    path=report_dir/'phase3_quality_report.md'
    path.write_text('\n'.join(md),encoding='utf-8')
    return path


COLUMNS = {
    'overview': ['table','source_file','records_count','columns_count','grain_hypothesis'],
    'column_schema': ['table','column','sql_type_raw','ordinal_position'],
    'missingness':['table','column','sql_type_raw','records_count','null_count','empty_count','whitespace_only_count','missing_or_empty_pct'],
    'duplicates':['table','total_rows','distinct_rows','exact_duplicate_excess','exact_duplicate_pct','note'],
    'keys':['table','candidate_key','columns_exist','missing_key_rows','duplicate_groups','excess_duplicate_rows','status'],
    'identifiers':['table','column','total_rows','nonempty_values','distinct_nonempty_values','repeated_nonempty_rows','distinct_ratio_pct','interpretation'],
    'references':['child_table','child_column','parent_table','parent_column','relationship','null_or_empty_children','orphan_rows','status','severity','child_records','orphan_pct'],
    'conversions':['table','column','expected_type','nonempty_values','conversion_failures','fail_pct','status'],
    'dates':['table','column','earliest','latest','nonempty_values'],
    'monetary':['table','column','total_rows','valid_numeric_count','minimum','maximum','average','total_value'],
    'monetary_issues':['table','column','negative_count','zero_count','invalid_conversion_count','policy'],
    'outliers':['table','column','q1','q3','iqr','lower_fence','upper_fence','valid_count','minimum','maximum','average','outlier_count','outlier_pct','treatment'],
    'categories':['table','column','value','records_count'],
    'temporal':['rule','count','severity','explanation','evaluated_records'],
    'statuses':['order_status','records_count'],
    'postals':['table','column','total_rows','missing_prefix','invalid_format','distinct_prefixes','status','note'],
    'payment_summary':['reconciliation_status','orders','absolute_difference_sum','maximum_absolute_difference','reason'],
    'payment_details':['order_id','item_rows','payment_rows','item_price','freight','paid','items_plus_freight','difference','invalid_item_rows','invalid_payment_rows','reconciliation_status'],
    'issues':['rule','table','affected_rows','severity','interpretation'],
    'kpis':['indicator','compliant_observations','evaluated_observations','compliance_pct','denominator_definition'],
}


def diagnose_raw(db_path: Path=DB_PATH, report_dir: Path=QUALITY_REPORTS_DIR,
                 fig_dir: Path=FIGURES_DIR, create_figures: bool=True) -> dict:
    """Generar reportes reproducibles sin realizar escrituras en el archivo DuckDB."""
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError('DuckDB no instalado. Activa conda: conda activate olist-quality') from exc
    db_path = Path(db_path)
    report_dir = Path(report_dir)
    fig_dir = Path(fig_dir)
    if not db_path.is_file():
        raise FileNotFoundError(f'Base {db_path} no existe. Ejecuta primero python -m src.extract')
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        present = {name for (name,) in con.execute('''
           SELECT table_name FROM information_schema.tables WHERE table_schema = 'raw'
        ''').fetchall()}
        missing_tables = sorted(set(s.name for s in TABLE_SPECS)-present)
        if missing_tables:
            raise RuntimeError('Tablas RAW faltantes: '+', '.join(missing_tables))
        cols = {s.name:raw_columns(con,s.name) for s in TABLE_SPECS}
        overview, column_schema, missingness = [], [], []
        for spec in TABLE_SPECS:
            total = count_row(con, f'SELECT COUNT(*) FROM raw.{qi(spec.name)}')
            overview.append(dict(table=spec.name, source_file=spec.filename,
                                 records_count=total,columns_count=len(cols[spec.name]),
                                 grain_hypothesis=spec.grain))
            for number,(column,dtype) in enumerate(cols[spec.name].items(),start=1):
                column_schema.append(dict(table=spec.name,column=column,
                                          sql_type_raw=dtype,ordinal_position=number))
            # Perfil previo de Fase 2, sin reimplementar su función.
            missingness.extend(_null_and_empty(con,spec.name,list(cols[spec.name])))
        duplicate_rows, key_rows = _duplicate_checks(con,cols)
        reference_rows = _reference_issues(con,cols)
        money_distribution, money_issues, outliers = _money_checks(con,cols)
        payment_summary, payment_details = _payments(con,cols)
        tables = dict(
            overview=overview, column_schema=column_schema, missingness=missingness,
            duplicates=duplicate_rows, keys=key_rows,
            identifiers=_id_cardinality(con,cols), references=reference_rows,
            conversions=_cast_checks(con,cols), dates=_date_ranges(con,cols),
            monetary=money_distribution, monetary_issues=money_issues,
            outliers=outliers, categories=_categories(con,cols),
            temporal=_temporal_checks(con,cols),statuses=_status_checks(con,cols),
            postals=_postals(con,cols), payment_summary=payment_summary,
            payment_details=payment_details,
        )
        tables['issues'] = _summarize_issues(tables)
        tables['kpis'] = _kpis(tables)
        audit_exists=con.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='audit' AND table_name='raw_load_manifest'").fetchone()[0] > 0
        load_manifest=(query_dicts(con, 'SELECT table_name,file_name,source_sha256,row_count,run_id,loaded_at FROM audit.raw_load_manifest ORDER BY table_name')
                       if audit_exists else [])
    finally:
        con.close()
    report_dir.mkdir(parents=True,exist_ok=True)
    for name,records in tables.items():
        output_csv(report_dir,name,records,COLUMNS[name])
    metadata = dict(run_id=uuid4().hex,
                    generated_at_utc=datetime.now(timezone.utc).isoformat(),
                    source_database=str(db_path), source_schema='raw',
                    tables_evaluated=len(overview),
                    raw_load_manifest=load_manifest,
                    disclaimer='Diagnóstico únicamente; no se corrige ni descarta ningún registro.')
    # Gráficos son opcionales; no contaminar el estado del pipeline por fallo gráfico.
    saved_figures = _make_figures(tables,fig_dir,db_path) if create_figures else []
    make_markdown(tables,report_dir,saved_figures,metadata)
    (report_dir/'phase3_manifest.json').write_text(
        json.dumps(metadata,indent=2,ensure_ascii=False),encoding='utf-8')
    (report_dir/'phase3_summary.json').write_text(
        json.dumps(tables,default=str,indent=2,ensure_ascii=False),encoding='utf-8')
    return tables


def main():
    parser=argparse.ArgumentParser(description='Fase 3: diagnóstico completo de Olist en RAW')
    parser.add_argument('--no-figures',action='store_true',help='No generar gráficas (útil para CI)')
    args=parser.parse_args()
    result=diagnose_raw(create_figures=not args.no_figures)
    print('[OK] Fase 3: diagnóstico inicial sobre RAW completado, sin modificar datos')
    for r in result['overview']:
        print(f"    raw.{r['table']}: {r['records_count']:,} filas, {r['columns_count']} columnas")
    print(f"[INFO] {len(result['issues'])} tipos de incidencia cuantificados (pueden solaparse)")
    print('[INFO] Reporte: reports/quality_reports/phase3_quality_report.md')
    print('[INFO] Gráficas: reports/figures/phase3_*.png')
    print('[INFO] Fase 4 pendiente: reglas SQL de RAW a CLEAN. No hay Parquet todavía.')


if __name__=='__main__':
    main()
