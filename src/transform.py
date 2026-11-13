"""Fase 4 — RAW -> (15 reglas SQL) -> CLEAN con auditoría, cuarentena y rollback.

NO exporta Parquet: las pruebas formales / publicación pertenecen a fases siguientes.
Ejecutar: python -m src.transform

Responsabilidades:
- SQL de transformación en sql/transformations/tables/*.sql (1 por tabla)
- SQL de las quince actividades en sql/transformations/rules/*.sql
- Python coordina, detecta conflictos, registra excepciones y preserva RAW.
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
from .temporal_policy import apply_temporal_policy, ensure_temporal_audit
from .model_metadata import TABLE_SPECS, RELATIONSHIPS
from .transform_schema import (
    SCHEMA, TIMESTAMPS, MONEYS, INTEGERS, DOUBLES,
    ZIP_FIELDS, LOWER_FIELDS, STATE_FIELDS, COMMENT_FIELDS,
    KEYS, REQUIRED,
)

LOG = logging.getLogger(__name__)
SQL_TABLES = ROOT_DIR / 'sql' / 'transformations' / 'tables'
SQL_RULES = ROOT_DIR / 'sql' / 'transformations' / 'rules'
TYPE_FIELDS = set(TIMESTAMPS) | set(MONEYS) | set(INTEGERS) | set(DOUBLES)
TEXT_FIELDS = set(LOWER_FIELDS) | set(STATE_FIELDS) | set(COMMENT_FIELDS) | set(ZIP_FIELDS)

# Regla, nombre, severidad y política principal. La fase 5 fijará el gate formal de publicación.
RULES = (
    ('R01', 'Duplicados', 'ALTA', 'Eliminar sólo excedentes verdaderos por clave comprobada; conflictos -> cuarentena completa'),
    ('R02', 'Nulos', 'ALTA', 'Conservar faltantes opcionales; claves o valores críticos ausentes -> cuarentena'),
    ('R03', 'Estandarización de texto', 'MEDIA', 'Trim, espacios repetidos y mayúsculas/minúsculas sólo en campos descriptivos'),
    ('R04', 'Conversión de tipos', 'ALTA', 'TRY_CAST y cuarentena de valores no vacíos que no se convierten'),
    ('R05', 'Fechas inconsistentes', 'MEDIA', 'Suprimir en CLEAN timestamps no verificables del transportista, preservando RAW y AUDIT'),
    ('R06', 'Precios inválidos', 'ALTA', 'Importes negativos/no interpretables -> cuarentena; ceros no se imputan'),
    ('R07', 'Valores atípicos IQR', 'BAJA', 'Sólo detectar; no capar ni eliminar extremos sin verificación externa'),
    ('R08', 'Normalización de categorías', 'MEDIA', 'Catálogo Olist original como correspondencia; no inventar traducciones'),
    ('R09', 'Integridad referencial', 'ALTA', 'Detectar referencias huérfanas; conservar para revisión, nunca crear padres ficticios'),
    ('R10', 'Estados del pedido', 'MEDIA', 'Normalizar capitalización y marcar contradicciones sin fechas supuestas'),
    ('R11', 'Prefijos postales', 'MEDIA', 'LPAD para secuencias 1-5 dígitos; otros formatos se conservan y advierten'),
    ('R12', 'Conciliación de pagos', 'MEDIA', 'Agregar artículos y pagos antes del JOIN; tolerancia de 0.01; no ajustar montos'),
    ('R13', 'Métodos de pago', 'MEDIA', 'Normalizar texto conocido; conservar métodos desconocidos con advertencia'),
    ('R14', 'Cadenas vacías', 'MEDIA', 'NULLIF/TRIM para textos descriptivos; IDs técnicos no se reescriben'),
    ('R15', 'Validación inicial CLEAN', 'CRITICA', 'Unicidad, completitud de claves y dominio monetario; abortar al fallar'),
)
RULE_NAMES = {r[0]: r[1] for r in RULES}


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def scalar(con, sql: str):
    return con.execute(sql).fetchone()[0]


def run_sql(con, filename: str, **replacements):
    """Leer una consulta SQL versionada; interpolar SOLO identificadores validados."""
    pattern = (SQL_RULES / filename).read_text(encoding='utf-8').strip().rstrip(';')
    sql = pattern.format(**{name: qi(value) if name not in ('key_list', 'required_condition', 'temp_table')
                            else value for name, value in replacements.items()})
    return con.execute(sql).fetchone()


def preflight(con):
    """Las columnas son hipótesis de catálogo, verificadas contra RAW antes de usarlas."""
    tables = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='raw'"
    ).fetchall()}
    missing = sorted(set(SCHEMA) - tables)
    if missing:
        raise RuntimeError('Faltan tablas RAW; ejecuta Fase 2: ' + ', '.join(missing))
    for table, expected in SCHEMA.items():
        actual = {r[0] for r in con.execute(f'DESCRIBE raw.{qi(table)}').fetchall()}
        if actual != set(expected):
            raise RuntimeError(
                f'La estructura RAW de {table} cambió. '\
                f'Faltan: {sorted(set(expected)-actual)}. '\
                f'Inesperadas: {sorted(actual-set(expected))}. '\
                'Revisar el CSV y actualizar las reglas, sin inventar columnas.'
            )
        data_types = {r[0]: r[1] for r in con.execute(f'DESCRIBE raw.{qi(table)}').fetchall()}
        if any(v.upper() != 'VARCHAR' for v in data_types.values()):
            raise RuntimeError(f'RAW.{table} debe preservar todos los valores como VARCHAR')


def init_audit(con):
    con.execute('CREATE SCHEMA IF NOT EXISTS clean')
    con.execute('CREATE SCHEMA IF NOT EXISTS audit')
    con.execute('''CREATE TABLE IF NOT EXISTS audit.transform_runs (
        run_id VARCHAR PRIMARY KEY, started_at TIMESTAMP, finished_at TIMESTAMP,
        status VARCHAR, error_message VARCHAR
    )''')
    con.execute('''CREATE TABLE IF NOT EXISTS audit.quality_rule_catalog (
        rule_id VARCHAR PRIMARY KEY, name VARCHAR, severity VARCHAR, policy VARCHAR
    )''')
    con.execute('''CREATE TABLE IF NOT EXISTS audit.rule_results (
        run_id VARCHAR, rule_id VARCHAR, table_name VARCHAR,
        detected BIGINT, corrected BIGINT, quarantined BIGINT, unresolved BIGINT,
        severity VARCHAR, status VARCHAR, explanation VARCHAR, checked_at TIMESTAMP
    )''')
    con.execute('''CREATE TABLE IF NOT EXISTS audit.quarantine_records (
        run_id VARCHAR, source_table VARCHAR, rule_id VARCHAR,
        record_sha256 VARCHAR, reason VARCHAR, raw_record_json VARCHAR,
        quarantined_at TIMESTAMP
    )''')
    con.execute('''CREATE TABLE IF NOT EXISTS audit.field_changes (
        run_id VARCHAR, source_table VARCHAR, rule_id VARCHAR,
        record_sha256 VARCHAR, column_name VARCHAR,
        old_value VARCHAR, new_value VARCHAR, changed_at TIMESTAMP
    )''')
    ensure_temporal_audit(con)
    for code, name, severity, policy in RULES:
        # Catálogo idempotente; no eliminar los históricos de ejecución.
        if not con.execute('SELECT 1 FROM audit.quality_rule_catalog WHERE rule_id=?', [code]).fetchone():
            con.execute('INSERT INTO audit.quality_rule_catalog VALUES (?,?,?,?)',
                        [code, name, severity, policy])


def sql_sum(expressions):
    return ' + '.join(f'COALESCE(CAST(({x}) AS INTEGER), 0)' for x in expressions) if expressions else '0'


def build_candidates(con, table: str, run_id: str):
    """Construir sólo TEMP; no hay esquema STAGING persistente."""
    definition = (SQL_TABLES / f'{table}.sql').read_text(encoding='utf-8').strip().rstrip(';')
    con.execute(f'CREATE OR REPLACE TEMP TABLE _typed_{table} AS {definition}')
    cast_conditions = []
    for col in SCHEMA[table]:
        if (table, col) in TYPE_FIELDS:
            original = qi('_src_'+col)
            cast_conditions.append(
                f"NULLIF(TRIM(t.{original}), '') IS NOT NULL AND t.{qi(col)} IS NULL"
            )
    # Completitud original != fallo de tipo: 'abc' no es un precio ausente,
    # sino un precio presente cuyo TRY_CAST falla (R04, no R02).
    required = []
    for col in REQUIRED.get(table, []):
        source_field = qi('_src_'+col) if (table,col) in TYPE_FIELDS else qi(col)
        required.append(f"NULLIF(TRIM(CAST(t.{source_field} AS VARCHAR)), '') IS NULL")
    domains = []
    if table == 'order_items':
        domains = ['t."order_item_id" < 1', 't."price" < 0', 't."freight_value" < 0']
    elif table == 'order_payments':
        domains = ['t."payment_sequential" < 1', 't."payment_installments" < 0',
                   't."payment_value" < 0']
    elif table == 'order_reviews':
        domains = ['t."review_score" NOT BETWEEN 1 AND 5']
    elif table == 'geolocation':
        domains = ['ABS(t."geolocation_lat") > 90', 'ABS(t."geolocation_lng") > 180']
    elif table == 'products':
        domains = [f't.{qi(c)} < 0' for c in SCHEMA[table]
                   if (table, c) in set(INTEGERS) | set(DOUBLES)]
    # Los importes deben conservar exactamente los centavos del CSV.
    # TRY_CAST(... DECIMAL(18,2)) puede redondear 10.005 a 10.01; eso no es una
    # corrección autorizada. Si difiere el valor de mayor precisión, cuarentena.
    for col in SCHEMA[table]:
        if (table,col) in MONEYS:
            src = qi('_src_'+col)
            raw_precise = f"TRY_CAST(NULLIF(TRIM(t.{src}), '') AS DECIMAL(26,6))"
            domains.append(
                f"({raw_precise} IS DISTINCT FROM CAST(t.{qi(col)} AS DECIMAL(26,6)))"
            )
    # Validar espacios en IDs sobre el valor ORIGINAL en VARCHAR. Algunos IDs,
    # como order_item_id, se convierten a INTEGER en CLEAN: TRIM(INTEGER) falla.
    # No corregir silenciosamente identificadores que tengan espacios periféricos.
    for col in SCHEMA[table]:
        if col.endswith('_id') or col.endswith('_unique_id'):
            source_col = '_src_' + col if (table, col) in TYPE_FIELDS else col
            original_id = f't.{qi(source_col)}'
            domains.append(
                f"({original_id} IS NOT NULL AND {original_id} <> TRIM({original_id}))"
            )
    normal = []
    for col in SCHEMA[table]:
        if (table, col) in TEXT_FIELDS:
            original = f"json_extract_string(t._raw_json, '$.{col}')"
            normal.append(f'({original} IS DISTINCT FROM t.{qi(col)})')
    canon_list = ', '.join(f'CAST(t.{qi(col)} AS VARCHAR)' for col in SCHEMA[table])
    con.execute(f'''CREATE OR REPLACE TEMP TABLE _candidate_{table} AS
        SELECT t.*,
               ({sql_sum(cast_conditions)}) AS _invalid_cast,
               ({sql_sum(required)}) AS _missing_required,
               ({sql_sum(domains)}) AS _invalid_domain,
               ({sql_sum(normal)}) AS _normalization,
               md5(to_json([{canon_list}])) AS _canonical_hash
        FROM _typed_{table} t
    ''')
    if table in KEYS:
        keys = ', '.join(qi(col) for col in KEYS[table])
        present = ' AND '.join(f'{qi(col)} IS NOT NULL' for col in KEYS[table])
        con.execute(f'''CREATE OR REPLACE TEMP TABLE _groups_{table} AS
            SELECT {keys}, COUNT(*) AS n, COUNT(DISTINCT _canonical_hash) AS variants
            FROM _candidate_{table} WHERE {present} GROUP BY {keys}''')
        joins = ' AND '.join(f'c.{qi(col)} = g.{qi(col)}' for col in KEYS[table])
        partition = ', '.join(f'c.{qi(col)}' for col in KEYS[table])
        group_join = f'LEFT JOIN _groups_{table} g ON {joins}'
        variants = 'COALESCE(g.variants, 1)'
        counter = f'ROW_NUMBER() OVER (PARTITION BY {partition} ORDER BY c._raw_hash)'
    else:
        group_join = ''
        variants = '1'
        counter = '1'
    con.execute(f'''CREATE OR REPLACE TEMP TABLE _classified_{table} AS
        WITH flags AS (
            SELECT c.*, {variants} AS _variants, {counter} AS _dup_seq
            FROM _candidate_{table} AS c {group_join}
        )
        SELECT flags.*,
               CASE WHEN _missing_required > 0 THEN 'MISSING_REQUIRED'
                    WHEN _invalid_cast > 0 THEN 'INVALID_CAST'
                    WHEN _invalid_domain > 0 THEN 'INVALID_DOMAIN'
                    WHEN _variants > 1 THEN 'BUSINESS_KEY_CONFLICT'
                    WHEN _dup_seq > 1 THEN 'REDUNDANT_KEY_ROW'
                    ELSE NULL END AS _reason
        FROM flags
    ''')
    selected_cols = ', '.join(qi(c) for c in SCHEMA[table])
    con.execute(f'''CREATE OR REPLACE TABLE clean.{qi(table)} AS
        SELECT {selected_cols} FROM _classified_{table} WHERE _reason IS NULL''')
    domain_rule = 'R06' if table in ('order_items', 'order_payments') else 'R15'
    rule_case = f"""CASE WHEN _reason IN ('REDUNDANT_KEY_ROW','BUSINESS_KEY_CONFLICT') THEN 'R01'
                       WHEN _reason = 'MISSING_REQUIRED' THEN 'R02'
                       WHEN _reason = 'INVALID_CAST' THEN 'R04'
                       WHEN _reason = 'INVALID_DOMAIN' THEN '{domain_rule}'
                       ELSE 'R15' END"""
    con.execute(f'''INSERT INTO audit.quarantine_records
          SELECT ?, ?, {rule_case}, _raw_hash, _reason,
                 CAST(_raw_json AS VARCHAR), current_timestamp
          FROM _classified_{table} WHERE _reason IS NOT NULL''', [run_id, table])

    # Historial por campo: ver ORIGINAL y NORMALIZADO sin reescribir RAW.
    for col in SCHEMA[table]:
        if (table,col) not in TEXT_FIELDS:
            continue
        source_value = f"json_extract_string(_raw_json, '$.{col}')"
        rule = ('R11' if (table,col) in ZIP_FIELDS else
                'R13' if (table,col)==('order_payments','payment_type') else
                'R08' if (table,col) in set(LOWER_FIELDS) and col.startswith('product_category_name') else
                'R14' if (table,col) in COMMENT_FIELDS else 'R03')
        con.execute(f'''INSERT INTO audit.field_changes
            SELECT ?, ?, ?, _raw_hash, ?, {source_value}, CAST({qi(col)} AS VARCHAR),
                   current_timestamp
            FROM _classified_{table}
            WHERE _reason IS NULL
              AND {source_value} IS DISTINCT FROM CAST({qi(col)} AS VARCHAR)''',
            [run_id,table,rule,col])


def metric_rows(con, run_id: str):
    """Ejecutar archivos SQL de las quince reglas y registrar resultados calculados."""
    metrics = []

    def add(code, table, detected=0, corrected=0, quarantined=0,
            unresolved=0, explanation='', force_fail=False):
        detected, corrected = int(detected or 0), int(corrected or 0)
        quarantined, unresolved = int(quarantined or 0), int(unresolved or 0)
        status = ('FAIL' if force_fail else 'WARNING' if unresolved > 0 or quarantined > 0
                  else 'PASS')
        metrics.append(dict(run_id=run_id, rule_id=code, table_name=table,
                            detected=detected, corrected=corrected,
                            quarantined=quarantined, unresolved=unresolved,
                            severity=next(x[2] for x in RULES if x[0] == code),
                            status=status, explanation=explanation, checked_at=now_utc()))

    for table in SCHEMA:
        quarantined = scalar(con, f'SELECT COUNT(*) FROM _classified_{table} WHERE _reason IS NOT NULL')
        if table in KEYS:
            key_list = ', '.join(qi(c) for c in KEYS[table])
            excess = run_sql(con, '01_duplicados.sql', temp_table=table, key_list=key_list)[0]
            discarded = scalar(con, f'''SELECT COUNT(*) FROM _classified_{table}
                                       WHERE _reason='REDUNDANT_KEY_ROW' ''')
            conflicting = scalar(con, f'''SELECT COUNT(*) FROM _classified_{table}
                                         WHERE _reason='BUSINESS_KEY_CONFLICT' ''')
            add('R01', table, excess, discarded, discarded + conflicting,
                0, 'Filas de clave conflictiva: cuarentena del grupo completo; no elegir arbitrariamente')
        else:
            # Detectar repeticiones exactas, PERO no eliminarlas sin clave demostrada.
            # Geolocalización puede contener mediciones repetidas legítimas.
            observed_dups = scalar(con, f'''SELECT COUNT(*) - COUNT(DISTINCT _raw_hash)
                FROM _candidate_{table}''')
            add('R01', table, observed_dups, 0, 0, observed_dups,
                'Repeticiones detectadas y preservadas; no hay clave de negocio única segura')
        # Las tablas TEMP se identifican por su nombre COMPLETO. No se debe
        # construir '_classified_' + qi(table), ya que resultaría
        # _classified_"orders" en lugar de "_classified_orders".
        classified_table = f'_classified_{table}'
        missing = run_sql(con, '02_nulos.sql', classified_table=classified_table)[0]
        gone_missing = scalar(con, f'''SELECT COUNT(*) FROM _classified_{table}
                                      WHERE _reason IS NOT NULL AND _missing_required>0''')
        add('R02', table, missing, 0, gone_missing, 0,
            'Nulos opcionales se mantienen; faltantes obligatorios se ponen en cuarentena')
        normalized, done_normal = run_sql(
            con, '03_texto.sql', classified_table=classified_table
        )
        add('R03', table, normalized, done_normal, 0, 0,
            'Sin transliteración de acentos ni cambio de identificadores técnicos')
        invalid_cast = run_sql(
            con, '04_tipos.sql', classified_table=classified_table
        )[0]
        invalid_quarantine = scalar(con, f'''SELECT COUNT(*) FROM _classified_{table}
                                             WHERE _reason IS NOT NULL AND _invalid_cast>0''')
        add('R04', table, invalid_cast, 0, invalid_quarantine, 0,
            'Sólo valores no vacíos que no se pueden tipificar entran en cuarentena')
        # R14 mide antes y después: espacios/cadenas vacías en RAW vs CLEAN.
        # El indicador representa celdas (puede haber varias en una misma fila).
        textfields = [c for c in SCHEMA[table] if (table,c) not in TYPE_FIELDS]
        raw_blanks, normalized_blanks, clean_blanks = 0, 0, 0
        for col in textfields:
            source_value = f"json_extract_string(_raw_json, '$.{col}')"
            marker = (f"OR LOWER(TRIM({source_value})) IN ('null','none','nan')"
                      if (table,col) in COMMENT_FIELDS else '')
            raw_blanks += scalar(con, f'''SELECT COUNT(*) FROM _classified_{table}
                WHERE {source_value} IS NOT NULL
                  AND (TRIM({source_value}) = '' {marker})''')
            normalized_blanks += scalar(con, f'''SELECT COUNT(*) FROM _classified_{table}
                WHERE _reason IS NULL AND {source_value} IS NOT NULL
                  AND (TRIM({source_value}) = '' {marker})''')
            clean_blanks += run_sql(con, '14_vacias.sql', table=table, column=col)[0]
        add('R14', table, raw_blanks, normalized_blanks,
            raw_blanks-normalized_blanks, clean_blanks,
            'Se cuentan celdas vacías RAW y se verifica que no queden textos vacíos CLEAN',
            force_fail=clean_blanks>0)
        # R15: las claves verificadas deben ser únicas y no pueden faltar en CLEAN.
        if table in KEYS:
            required_condition = ' OR '.join(f'{qi(c)} IS NULL' for c in REQUIRED[table])
            missing_critical, dup_critical = run_sql(
                con, '15_final.sql', table=table,
                key_list=', '.join(qi(c) for c in KEYS[table]),
                required_condition=required_condition,
            )
            bad = missing_critical + dup_critical
            add('R15', table, bad, 0, 0, bad,
                'Prueba inicial: claves únicas y campos críticos presentes', force_fail=bad>0)
        else:
            # Sin clave única comprobada, sí puede exigirse completitud de IDs de referencia.
            required_fields = REQUIRED.get(table, [])
            missing_check = (' OR '.join(f'{qi(c)} IS NULL' for c in required_fields)
                             if required_fields else 'FALSE')
            missing_critical = scalar(con, f'SELECT COUNT(*) FROM clean.{qi(table)} WHERE {missing_check}')
            add('R15', table, missing_critical, 0, 0, missing_critical,
                'No se fuerza unicidad artificial; sólo campos obligatorios completos',
                force_fail=missing_critical > 0)

    masked_events = con.execute('SELECT COUNT(*) FROM audit.temporal_policy_events WHERE transform_run_id=?', [run_id]).fetchone()[0]
    residual_temporal = run_sql(con, '05_fechas.sql')[0]
    add('R05', 'orders', masked_events + residual_temporal, masked_events, 0,
        residual_temporal,
        'Timestamp del transportista no verificable: NULL sólo en CLEAN; '
        'fecha original y motivo en AUDIT. Las anomalías de origen siguen reportadas.')
    for table, field in sorted(MONEYS):
        negatives = run_sql(con, '06_precios.sql', table=table, column=field)[0]
        # R06 contabiliza negativos RAW convertibles y los negativos que quedan en CLEAN.
        # Incluir también importes que requerirían redondeo silencioso.
        src = qi('_src_'+field)
        invalid_money = scalar(con, f'''SELECT COUNT(*) FROM _classified_{table}
            WHERE {qi(field)} < 0 OR
                  TRY_CAST(NULLIF(TRIM({src}), '') AS DECIMAL(26,6))
                  IS DISTINCT FROM CAST({qi(field)} AS DECIMAL(26,6))''')
        add('R06', f'{table}.{field}', invalid_money, 0, invalid_money,
            negatives, 'Ceros se conservan; negativos y redondeos a centavos no autorizados se ponen en cuarentena',
            force_fail=negatives>0)
        info = run_sql(con, '07_atipicos.sql', table=table, column=field)
        q1, q3, iqr, outliers = info
        add('R07', f'{table}.{field}', outliers, 0, 0, outliers,
            f'IQR: Q1={q1}; Q3={q3}; IQR={iqr}. Sólo detección, sin winsorizar')

    unmatched = run_sql(con, '08_categorias.sql')[0]
    mapped_normalized = con.execute("SELECT COUNT(DISTINCT record_sha256) FROM audit.field_changes WHERE run_id=? AND rule_id='R08' AND source_table='products'", [run_id]).fetchone()[0]
    add('R08', 'products', unmatched + mapped_normalized, mapped_normalized, 0, unmatched,
        'Sin catálogo equivalente, la categoría original normalizada se conserva')
    for child, child_col, parent, parent_col, _note in RELATIONSHIPS:
        if parent == 'category_translation':
            continue  # la referencia de traducciones es opcional y R08 la contabiliza
        orphan = run_sql(con, '09_integridad.sql', child_table=child,
                         child_column=child_col, parent_table=parent,
                         parent_column=parent_col)[0]
        add('R09', f'{child}.{child_col}->{parent}.{parent_col}', orphan, 0, 0, orphan,
            'Se conserva el registro hijo; no crear padres inventados ni eliminar pagos/artículos')
    status = run_sql(con, '10_estados.sql')[0]
    add('R10', 'orders', status, 0, 0, status,
        'No existe fecha de cancelación en el CSV; estados no obligan a todas las fechas')
    for table, column in ZIP_FIELDS:
        malformed = run_sql(con, '11_postales.sql', table=table, column=column)[0]
        fixed = con.execute('''SELECT COUNT(*) FROM audit.field_changes
                               WHERE run_id=? AND rule_id='R11' AND source_table=?''',
                            [run_id,table]).fetchone()[0]
        add('R11', f'{table}.{column}', malformed + fixed, fixed, 0, malformed,
            'LPAD para prefijos numéricos cortos; anomalías restantes se conservan')
    discrepancies, pending, reconciled = run_sql(con, '12_pagos.sql')
    add('R12', 'orders', discrepancies + pending, 0, 0, discrepancies + pending,
        f'Conciliados={reconciled}; discrepantes={discrepancies}; pendientes={pending}. '\
        'Los vales/descuentos requieren contexto, no corregir diferencias automáticamente')
    unknown = run_sql(con, '13_metodos.sql')[0]
    method_changes = con.execute("SELECT COUNT(*) FROM audit.field_changes WHERE run_id=? AND rule_id='R13'", [run_id]).fetchone()[0]
    add('R13', 'order_payments', unknown + method_changes, method_changes, 0, unknown,
        'Se conservan métodos desconocidos o not_defined; sin sustituciones arbitrarias')
    criticals = [m for m in metrics if m['status'] == 'FAIL']
    for m in metrics:
        con.execute('''INSERT INTO audit.rule_results
            (run_id,rule_id,table_name,detected,corrected,quarantined,unresolved,
             severity,status,explanation,checked_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
            [m[k] for k in ('run_id','rule_id','table_name','detected','corrected',
                            'quarantined','unresolved','severity','status',
                            'explanation','checked_at')])
    if criticals:
        detail = ', '.join(f"{r['rule_id']}:{r['table_name']}({r['unresolved']})"
                           for r in criticals[:12])
        raise RuntimeError('La validación inicial CLEAN falló: ' + detail)
    return metrics


def _write_csv(path: Path, records: list[dict], fields: list[str]):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for row in records:
            writer.writerow({k: '' if row.get(k) is None else row[k] for k in fields})


def write_reports(outdir: Path, run_id: str, overview: list[dict], rules: list[dict],
                  quarantine: list[dict]):
    outdir.mkdir(parents=True, exist_ok=True)
    _write_csv(outdir/'phase4_clean_overview.csv', overview,
               ['table','raw_rows','clean_rows','quarantined_rows','difference'])
    _write_csv(outdir/'phase4_rule_results.csv', rules,
               ['run_id','rule_id','table_name','detected','corrected','quarantined',
                'unresolved','severity','status','explanation','checked_at'])
    _write_csv(outdir/'phase4_quarantine_summary.csv', quarantine,
               ['table','reason','rows'])
    digest = {'run_id':run_id, 'timestamp_utc': now_utc().isoformat(),
              'tables':overview, 'quarantine':quarantine,
              'rules':rules, 'notes':['No se modificó RAW','No se publicaron Parquet',
                'La fase 5 realizará pruebas formales y la fase 7 publicará Parquet aprobados']}
    (outdir/'phase4_summary.json').write_text(
        json.dumps(digest, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    md = ['# Fase 4 — Transformación RAW → CLEAN', '',
          f'Run ID: `{run_id}`. Generado UTC: `{digest["timestamp_utc"]}`.', '',
          '**Alcance:** dos capas persistentes, RAW y CLEAN. Las tablas `audit` son auxiliares de control.',
          'No se exporta Parquet en esta fase: publicación tras las pruebas formales.', '',
          '## Conteos por tabla', '',
          '| Tabla | RAW | CLEAN | Cuarentena | Diferencia |',
          '|---|---:|---:|---:|---:|']
    for row in overview:
        md.append(f"| {row['table']} | {row['raw_rows']:,} | {row['clean_rows']:,} | "
                  f"{row['quarantined_rows']:,} | {row['difference']:,} |")
    md += ['', '## Cuarentena por causa', '', '| Tabla | Motivo | Filas |', '|---|---|---:|']
    for row in quarantine:
        md.append(f"| {row['table']} | {row['reason']} | {row['rows']:,} |")
    if not quarantine: md.append('| Ninguna registrada | — | 0 |')
    md += ['', '## Las 15 reglas', '',
           'Las cantidades pueden **solaparse** entre reglas; nunca sumar sus detecciones como filas únicas.',
           '| Regla | Tabla | Detectadas | Corregidas | Cuarentena | Pendientes | Resultado |',
           '|---|---|---:|---:|---:|---:|---|']
    for row in rules:
        md.append(f"| {row['rule_id']} | {row['table_name']} | {row['detected']} | "
                  f"{row['corrected']} | {row['quarantined']} | {row['unresolved']} | {row['status']} |")
    md += ['', '## Decisiones de limpieza y límites', '',
           '- **Duplicados:** se usa clave de negocio verificada; mismo pedido con distintos artículos o pagos no es duplicado.',
           '- **Conflictos de clave:** no se escoge arbitrariamente entre versiones distintas. Todo el grupo se pone en cuarentena.',
           '- **Fechas y estados:** las contradicciones se registran; no se fabrican eventos faltantes.',
           '- **Valores extremos:** se registran mediante IQR y se conservan.',
           '- **Referencias huérfanas:** se documentan para resolver con la fuente, sin eliminar hijos por comodidad.',
           '- **Conciliación:** se agregan importes por pedido ANTES de relacionar tablas; tolerancia de 0.01.',
           '- **Exactitud:** no comprobada por falta de referencia externa confiable.',
           '- **Integridad:** una cuarentena de un padre puede aumentar alertas de huérfanos; evaluar excepciones.',
           '- **Auditoría:** los valores RAW originales se conservan como JSON en `audit.quarantine_records`.', '',
           '## Próxima fase', '',
           'Fase 5: pruebas automatizadas formales, definición de fallos críticos, '
           'conciliación de excepciones y comparación completa RAW vs CLEAN. '
           '**No considerar los Parquet publicados**.', '']
    path = outdir/'phase4_transformation_report.md'
    path.write_text('\n'.join(md), encoding='utf-8')
    return path


def transform_data(db_path=DB_PATH, report_dir=QUALITY_REPORTS_DIR):
    """Ejecutar 15 reglas y construir CLEAN en una transacción; reiterable sin duplicar CLEAN."""
    import duckdb
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f'No se encontró {db_path}; ejecutar primero python -m src.extract')
    con = duckdb.connect(str(db_path))
    run_id = uuid4().hex
    try:
        preflight(con)
        init_audit(con)
        con.execute('''INSERT INTO audit.transform_runs VALUES (?,?,NULL,'RUNNING',NULL)''',
                    [run_id, now_utc()])
        con.execute('BEGIN TRANSACTION')
        try:
            # La vista de calidad depende de CLEAN.orders: retirarla dentro de la
            # transacción antes del CREATE OR REPLACE TABLE (reejecución idempotente).
            con.execute('DROP VIEW IF EXISTS clean.orders_temporal_quality')
            for table in SCHEMA:
                build_candidates(con, table, run_id)
                LOG.info('CLEAN creado: %s', table)
            temporal_result = apply_temporal_policy(con, run_id)
            LOG.info('R05: eventos=%s; pedidos con timestamp enmascarado=%s',
                     temporal_result['events'], temporal_result['orders_masked'])
            rules = metric_rows(con, run_id)
            overview = []
            for table in SCHEMA:
                raw_rows = scalar(con, f'SELECT COUNT(*) FROM raw.{qi(table)}')
                clean_rows = scalar(con, f'SELECT COUNT(*) FROM clean.{qi(table)}')
                q_rows = con.execute(
                    'SELECT COUNT(*) FROM audit.quarantine_records WHERE run_id=? AND source_table=?',
                    [run_id, table]).fetchone()[0]
                if raw_rows != clean_rows+q_rows:
                    raise RuntimeError(f'Balance RAW/CLEAN/cuarentena no cierra: {table}')
                overview.append(dict(table=table, raw_rows=raw_rows, clean_rows=clean_rows,
                                     quarantined_rows=q_rows, difference=raw_rows-clean_rows))
            quarantine = [dict(table=r[0],reason=r[1],rows=r[2]) for r in con.execute(
                '''SELECT source_table, reason, COUNT(*) FROM audit.quarantine_records
                   WHERE run_id=? GROUP BY 1,2 ORDER BY 1,2''', [run_id]
            ).fetchall()]
            con.execute('''UPDATE audit.transform_runs SET status='COMPLETED',finished_at=?
                           WHERE run_id=?''', [now_utc(),run_id])
            con.execute('COMMIT')
        except Exception:
            con.execute('ROLLBACK')
            raise
        report_path = write_reports(Path(report_dir), run_id, overview, rules, quarantine)
        return {'run_id':run_id, 'status':'COMPLETED', 'tables':overview,
                'quarantine':quarantine, 'rules':rules, 'report':str(report_path), 'temporal_policy':temporal_result}
    except Exception as exc:
        # La transacción deja sin cambios CLEAN previo; guardar razón del fallo por separado.
        try:
            con.execute('''UPDATE audit.transform_runs SET status='FAILED',finished_at=?,
                           error_message=? WHERE run_id=?''', [now_utc(), str(exc)[:4000],run_id])
        except Exception:
            pass
        raise
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser(description='Fase 4: 15 reglas RAW -> CLEAN (sin Parquet)')
    parser.add_argument('--db', type=Path, default=DB_PATH,
                        help='Base DuckDB; por defecto database/ecommerce.duckdb')
    parser.add_argument('--reports', type=Path, default=QUALITY_REPORTS_DIR,
                        help='Carpeta de reportes')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s | %(message)s')
    result = transform_data(args.db,args.reports)
    print('\n[OK] RAW -> CLEAN construido bajo auditoría.')
    for entry in result['tables']:
        print(f"    {entry['table']}: RAW={entry['raw_rows']}, CLEAN={entry['clean_rows']}, "
              f"cuarentena={entry['quarantined_rows']}")
    print('[OK] 15 reglas evaluadas. Reporte: '+result['report'])
    print('[INFO] No se exportaron archivos Parquet: faltan pruebas formales.')


if __name__ == '__main__':
    main()
