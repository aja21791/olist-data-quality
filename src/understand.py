"""Fase 2: perfilado inicial sobre RAW sin modificar tablas ni CSV.

Produce diagnóstico verificable: conteos, tipos, vacíos, claves candidatas,
referencias, fechas, montos, categorías, diccionario y diagrama ER conceptual.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .config import DB_PATH, DICTIONARY_DIR, QUALITY_REPORTS_DIR
from .extract import qi
from .model_metadata import (
    TABLE_SPECS, RELATIONSHIPS, TIMESTAMP_CANDIDATES,
    MONETARY_CANDIDATES, CATEGORY_CANDIDATES,
)


def export_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def columns_of(con, table_name: str) -> dict[str, str]:
    return {row[0]: row[1] for row in con.execute(f'DESCRIBE raw.{qi(table_name)}').fetchall()}


def _null_and_empty(con, table: str, cols: list[str]) -> list[dict]:
    if not cols:
        return []
    selects = ['COUNT(*) AS total_rows']
    for i, name in enumerate(cols):
        q = qi(name)
        selects.extend([
            f'SUM(CASE WHEN {q} IS NULL THEN 1 ELSE 0 END) AS n{i}',
            f"SUM(CASE WHEN {q} = '' THEN 1 ELSE 0 END) AS e{i}",
            f"SUM(CASE WHEN {q} IS NOT NULL AND {q} <> '' AND TRIM({q}) = '' THEN 1 ELSE 0 END) AS w{i}",
        ])
    observed = con.execute(f'SELECT {", ".join(selects)} FROM raw.{qi(table)}').fetchone()
    count = observed[0]
    return [
        {'table': table, 'column': name, 'sql_type_raw': 'VARCHAR',
         'records_count': count, 'null_count': observed[1 + 3 * i] or 0,
         'empty_count': observed[2 + 3 * i] or 0,
         'whitespace_only_count': observed[3 + 3 * i] or 0,
         'missing_or_empty_pct': round(100 * (
             (observed[1 + 3 * i] or 0) + (observed[2 + 3 * i] or 0) +
             (observed[3 + 3 * i] or 0)) / count, 4) if count else 0}
        for i, name in enumerate(cols)
    ]


def _key_metrics(con, table: str, key: tuple[str, ...], cols: dict[str, str]) -> dict:
    actual = all(k in cols for k in key)
    result = {'table': table, 'candidate_key': ' + '.join(key),
              'columns_exist': actual, 'missing_key_rows': None,
              'duplicate_groups': None, 'excess_duplicate_rows': None,
              'status': 'MISSING_COLUMNS'}
    if not actual:
        return result
    nonempty = ' AND '.join(f"NULLIF(TRIM({qi(k)}), '') IS NOT NULL" for k in key)
    missing = con.execute(f'SELECT COUNT(*) FROM raw.{qi(table)} WHERE NOT ({nonempty})').fetchone()[0]
    group_cols = ', '.join(qi(k) for k in key)
    duplicate_groups, duplicate_extra = con.execute(f'''
        SELECT COUNT(*), COALESCE(SUM(n - 1), 0)
        FROM (SELECT COUNT(*) n FROM raw.{qi(table)}
              WHERE {nonempty} GROUP BY {group_cols} HAVING COUNT(*) > 1)
    ''').fetchone()
    result.update(missing_key_rows=missing, duplicate_groups=duplicate_groups,
                  excess_duplicate_rows=duplicate_extra,
                  status=('VALID_CANDIDATE' if missing == 0 and duplicate_extra == 0 else 'REVIEW'))
    return result


def _fk_metrics(con, child: str, child_col: str, parent: str, parent_col: str,
                cols_map: dict[str, dict[str, str]], note: str) -> dict:
    entry = {'child_table': child, 'child_column': child_col,
             'parent_table': parent, 'parent_column': parent_col,
             'relationship': note, 'null_or_empty_children': None,
             'orphan_rows': None, 'status': 'NOT_EVALUATED'}
    if child_col not in cols_map[child] or parent_col not in cols_map[parent]:
        entry['status'] = 'MISSING_COLUMNS'
        return entry
    field = 'c.' + qi(child_col)
    ref = 'p.' + qi(parent_col)
    missing = f"NULLIF(TRIM({field}), '') IS NULL"
    orphan = f'''NOT EXISTS (SELECT 1 FROM raw.{qi(parent)} p
                           WHERE {ref} = {field})'''
    # Incluir nulos explícitos como dimensión distinta, no mezclarlos con huérfanos.
    count_null, count_orphan = con.execute(f'''
        SELECT SUM(CASE WHEN {missing} THEN 1 ELSE 0 END),
               SUM(CASE WHEN NOT ({missing}) AND {orphan} THEN 1 ELSE 0 END)
        FROM raw.{qi(child)} c
    ''').fetchone()
    entry.update(null_or_empty_children=count_null or 0, orphan_rows=count_orphan or 0,
                 status='REVIEW' if count_orphan else 'NO_ORPHANS_OBSERVED')
    return entry


def _date_metrics(con, table: str, column: str) -> dict:
    expr = f'TRY_CAST(NULLIF(TRIM({qi(column)}), \'\') AS TIMESTAMP)'
    source = f'raw.{qi(table)}'
    min_val, max_val, invalid, present = con.execute(f'''
        SELECT MIN({expr}), MAX({expr}),
               SUM(CASE WHEN NULLIF(TRIM({qi(column)}), '') IS NOT NULL AND {expr} IS NULL
                   THEN 1 ELSE 0 END),
               SUM(CASE WHEN NULLIF(TRIM({qi(column)}), '') IS NOT NULL
                   THEN 1 ELSE 0 END)
        FROM {source}
    ''').fetchone()
    return {'table': table, 'column': column, 'min_timestamp': str(min_val) if min_val else '',
            'max_timestamp': str(max_val) if max_val else '',
            'nonempty_values': present or 0, 'invalid_casts': invalid or 0}


def _money_metrics(con, table: str, column: str) -> dict:
    expr = f'TRY_CAST(NULLIF(TRIM({qi(column)}), \'\') AS DECIMAL(18, 2))'
    minimum, maximum, average, invalid, present = con.execute(f'''
        SELECT MIN({expr}), MAX({expr}), AVG({expr}),
               SUM(CASE WHEN NULLIF(TRIM({qi(column)}), '') IS NOT NULL AND {expr} IS NULL
                   THEN 1 ELSE 0 END),
               SUM(CASE WHEN NULLIF(TRIM({qi(column)}), '') IS NOT NULL THEN 1 ELSE 0 END)
        FROM raw.{qi(table)}
    ''').fetchone()
    return {'table': table, 'column': column, 'min_value': str(minimum) if minimum is not None else '',
            'max_value': str(maximum) if maximum is not None else '',
            'avg_value': str(average) if average is not None else '',
            'nonempty_values': present or 0, 'invalid_casts': invalid or 0}


def build_er_diagram(cols_map: dict[str, dict[str, str]], key_rows: list[dict],
                     rel_rows: list[dict]) -> str:
    """Diagrama conceptual. No impone FK ni presume PK si la prueba falló."""
    verified = {row['table']: row for row in key_rows if row['status'] == 'VALID_CANDIDATE'}
    rows = ['erDiagram', '    %% Modelo conceptual; NO implica restricciones físicas aplicadas en RAW.']
    for spec in TABLE_SPECS:
        rows.append(f'    {spec.name} {{')
        members = cols_map[spec.name]
        for name, dtype in members.items():
            # Mermaid sólo admite identificadores simples. Olist suele usar snake_case,
            # pero nombres inesperados se reemplazan y se conserva el original en diccionario.
            alias = ''.join(ch if ch.isalnum() or ch == '_' else '_' for ch in name)
            marker = ' PK' if spec.name in verified and name in spec.candidate_key else ''
            rows.append(f'        string {alias}{marker}')
        rows.append('    }')
    for entry in rel_rows:
        if entry['status'] == 'MISSING_COLUMNS':
            continue
        child = entry['child_table']
        parent = entry['parent_table']
        label = entry['child_column']
        # 1:N esperado; la verificación de existencia de referencias está en CSV.
        rows.append(f'    {parent} ||--o{{ {child} : "{label}"')
    return '\n'.join(rows) + '\n'


def profile_raw(db_path=DB_PATH, reports_dir=QUALITY_REPORTS_DIR,
                dictionary_dir=DICTIONARY_DIR) -> dict:
    import duckdb

    if not db_path.exists():
        raise FileNotFoundError(f'Base DuckDB no encontrada: {db_path}. Ejecuta python -m src.extract')
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        existing = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'raw'").fetchall()}
        missing = sorted({s.name for s in TABLE_SPECS} - existing)
        if missing:
            raise RuntimeError('Faltan tablas RAW: ' + ', '.join(missing))
        columns = {s.name: columns_of(con, s.name) for s in TABLE_SPECS}
        overview = []
        null_rows = []
        key_rows = []
        for spec in TABLE_SPECS:
            column_types = columns[spec.name]
            count = con.execute(f'SELECT COUNT(*) FROM raw.{qi(spec.name)}').fetchone()[0]
            overview.append({'table': spec.name, 'records_count': count,
                             'columns_count': len(column_types), 'source': spec.filename,
                             'grain_hypothesis': spec.grain})
            null_rows.extend(_null_and_empty(con, spec.name, list(column_types)))
            if spec.candidate_key:
                key_rows.append(_key_metrics(con, spec.name, spec.candidate_key, column_types))
        # Las reseñas requieren evaluación explícita: review_id no se asume único.
        for key in [('review_id',), ('review_id', 'order_id')]:
            result = _key_metrics(con, 'order_reviews', key, columns['order_reviews'])
            result['status'] = 'DIAGNOSTIC_ONLY_' + result['status']
            key_rows.append(result)
        rel_rows = []
        for child, child_col, parent, parent_col, note in RELATIONSHIPS:
            rel_rows.append(_fk_metrics(con, child, child_col, parent, parent_col, columns, note))
        date_rows, money_rows, category_rows = [], [], []
        for table, fieldnames in TIMESTAMP_CANDIDATES.items():
            for field in fieldnames:
                if field in columns[table]:
                    date_rows.append(_date_metrics(con, table, field))
        for table, fieldnames in MONETARY_CANDIDATES.items():
            for field in fieldnames:
                if field in columns[table]:
                    money_rows.append(_money_metrics(con, table, field))
        for table, fieldnames in CATEGORY_CANDIDATES.items():
            for field in fieldnames:
                if field in columns[table]:
                    values = con.execute(f'''
                        SELECT {qi(field)} AS value, COUNT(*) AS records_count
                        FROM raw.{qi(table)} GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 30
                    ''').fetchall()
                    category_rows.extend({'table': table, 'column': field, 'value': value,
                                          'records_count': count} for value, count in values)
        # La estructura tipada original no existe en un CSV: VARCHAR representa el texto leído.
        column_rows = [
            {'table': spec.name, 'source_file': spec.filename, 'column': name,
             'raw_sql_type': dtype, 'candidate_key_member': name in spec.candidate_key,
             'interpretation': 'Por confirmar con responsable de negocio'}
            for spec in TABLE_SPECS for name, dtype in columns[spec.name].items()
        ]
    finally:
        con.close()

    reports_dir.mkdir(parents=True, exist_ok=True)
    dictionary_dir.mkdir(parents=True, exist_ok=True)
    tables = {
        'raw_tables': overview, 'raw_missingness': null_rows,
        'raw_keys': key_rows, 'raw_references': rel_rows,
        'raw_dates': date_rows, 'raw_monetary': money_rows,
        'raw_categories': category_rows,
    }
    for filename, records in tables.items():
        export_csv(reports_dir / f'{filename}.csv', records, {
            'raw_tables': ['table', 'records_count', 'columns_count', 'source', 'grain_hypothesis'],
            'raw_missingness': ['table', 'column', 'sql_type_raw', 'records_count',
                                'null_count', 'empty_count', 'whitespace_only_count', 'missing_or_empty_pct'],
            'raw_keys': ['table', 'candidate_key', 'columns_exist', 'missing_key_rows',
                         'duplicate_groups', 'excess_duplicate_rows', 'status'],
            'raw_references': ['child_table', 'child_column', 'parent_table', 'parent_column',
                               'relationship', 'null_or_empty_children', 'orphan_rows', 'status'],
            'raw_dates': ['table', 'column', 'min_timestamp', 'max_timestamp', 'nonempty_values', 'invalid_casts'],
            'raw_monetary': ['table', 'column', 'min_value', 'max_value', 'avg_value', 'nonempty_values', 'invalid_casts'],
            'raw_categories': ['table', 'column', 'value', 'records_count'],
        }[filename])
    export_csv(dictionary_dir / 'raw_columns.csv', column_rows,
               ['table', 'source_file', 'column', 'raw_sql_type', 'candidate_key_member', 'interpretation'])
    er = build_er_diagram(columns, key_rows, rel_rows)
    (dictionary_dir / 'olist_er.mmd').write_text(er, encoding='utf-8')
    md = [
        '# Dataset Olist — diccionario inicial (Fase 2)', '',
        'Fuente de verdad: los 9 CSV importados a RAW. Este documento se genera a partir de los nombres y tipos realmente observados.',
        'Los CSV no especifican tipos SQL; por política de conservación, RAW utiliza VARCHAR. Las PK/FK se reportan como **candidatas verificadas**, no como constraints físicos.',
        '', '| Tabla | Archivo | Granularidad esperada | Filas observadas |', '|---|---|---|---:|',
    ]
    for spec, info in zip(TABLE_SPECS, overview):
        md.append(f'| raw.{spec.name} | {spec.filename} | {spec.grain} | {info["records_count"]:,} |')
    md.extend(['', '## Precauciones relacionales', ''])
    for spec in TABLE_SPECS:
        md.append(f'- **{spec.name}:** {spec.key_note or spec.grain}.')
    md.extend(['', '## Columnas realmente detectadas', '',
               'Para la lista completa, consultar `raw_columns.csv`.',
               '', '## Claves candidatas', '', '| Tabla | Clave | Estado | Filas excedentes |', '|---|---|---|---:|'])
    for item in key_rows:
        md.append(f'| {item["table"]} | {item["candidate_key"]} | {item["status"]} | {item["excess_duplicate_rows"]} |')
    md.extend(['', '## Relaciones (sin imponer FK físicas)', '',
               '| Relación | Huérfanos | Nulos/vacíos | Estado |', '|---|---:|---:|---|'])
    for row in rel_rows:
        md.append(f'| {row["child_table"]}.{row["child_column"]} → {row["parent_table"]}.{row["parent_column"]} | '
                  f'{row["orphan_rows"]} | {row["null_or_empty_children"]} | {row["status"]} |')
    md.extend(['', 'Los prefijos geográficos no se usan como PK/FK, pues son potencialmente repetidos.',
               'El vínculo entre productos y traducción de categoría es de cobertura opcional; no se exige como regla crítica.',
               '', '## Diagrama ER', '', '```mermaid', er.rstrip(), '```', ''])
    (dictionary_dir / 'data_dictionary_initial.md').write_text('\n'.join(md), encoding='utf-8')
    (reports_dir / 'raw_profile_summary.json').write_text(
        json.dumps(tables, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    return tables


def main():
    result = profile_raw()
    print('[OK] Perfilado RAW. Todos los resultados proceden de la base cargada.')
    for row in result['raw_tables']:
        print(f"raw.{row['table']}: {row['records_count']} filas y {row['columns_count']} columnas")
    print('[INFO] Revisar reports/quality_reports/raw_*.csv y reports/data_dictionary/')
    print('[INFO] Todavía no se han limpiado datos ni creado tablas CLEAN.')


if __name__ == '__main__':
    main()
