"""Fase 2: CSV originales -> Python + DuckDB -> RAW, con conteos y trazabilidad.

- RAW sólo VARCHAR. Incluso el campo vacío del CSV permanece como cadena vacía.
- Archivos originales nunca se abren para escritura.
- Una transacción crea o reemplaza el lote inicial completo; fallo => rollback.
- Reejecuciones idénticas son idempotentes; cambios de fuente requieren --force-reload.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from uuid import uuid4

from .bootstrap import bootstrap
from .config import DATA_RAW_DIR, DB_PATH, EXPECTED_CSV_FILES, QUALITY_REPORTS_DIR
from .model_metadata import TABLE_SPECS
from .source_inventory import csv_inventory


def qi(name: str) -> str:
    """Escapar identificadores SQL (sólo encabezados inspeccionados)."""
    return '"' + name.replace('"', '""') + '"'


def qs(value: str) -> str:
    """Escapar literales SQL, incluyendo rutas Windows convertidas a / ."""
    return "'" + value.replace("'", "''") + "'"


def csv_read_query(file_path, columns: list[str]) -> str:
    """Declarar el esquema desde CSV real; no inferir tipos ni perder ceros iniciales.

    force_not_null para cada columna conserva celdas vacías como ''. Los datos
    CSV ya no distinguen formatos numéricos: se guardan íntegros como texto.
    """
    colmap = ', '.join(f'{qs(name)}: {qs("VARCHAR")}' for name in columns)
    force_not_null = ', '.join(qs(name) for name in columns)
    return (
        f"read_csv({qs(file_path.resolve().as_posix())}, "
        "auto_detect = false, header = true, delim = ',', "
        "quote = '\"', escape = '\"', "
        f"columns = {{{colmap}}}, "
        f"force_not_null = [{force_not_null}], allow_quoted_nulls = false)"
    )


def _preflight(input_dir=DATA_RAW_DIR) -> dict[str, dict]:
    actual_files = {p.name for p in input_dir.glob('*.csv')}
    missing = sorted(set(EXPECTED_CSV_FILES) - actual_files)
    extra = sorted(actual_files - set(EXPECTED_CSV_FILES))
    if missing:
        raise FileNotFoundError('Faltan CSV Olist: ' + ', '.join(missing))
    if extra:
        raise ValueError('CSV no reconocidos en data/raw/: ' + ', '.join(extra))
    result = {}
    for spec in TABLE_SPECS:
        entry = csv_inventory(input_dir / spec.filename)
        if entry['row_width_mismatches']:
            raise ValueError(f"{spec.filename}: {entry['row_width_mismatches']} registros con longitud distinta al encabezado")
        result[spec.name] = entry
    return result


def load_raw(force_reload: bool = False, input_dir=DATA_RAW_DIR, db_path=DB_PATH) -> list[dict]:
    """Carga transaccional y repetible de las 9 fuentes Olist a RAW."""
    import duckdb

    source_manifest = _preflight(input_dir)
    if db_path == DB_PATH:
        bootstrap()  # Asegurar esquemas y BD de proyecto
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    run_id = uuid4().hex
    outcomes = []
    try:
        con.execute('CREATE SCHEMA IF NOT EXISTS raw')
        con.execute('CREATE SCHEMA IF NOT EXISTS audit')
        con.execute('''CREATE TABLE IF NOT EXISTS audit.raw_load_manifest (
            table_name VARCHAR PRIMARY KEY,
            file_name VARCHAR NOT NULL,
            source_sha256 VARCHAR NOT NULL,
            row_count BIGINT NOT NULL,
            column_count INTEGER NOT NULL,
            columns_json VARCHAR NOT NULL,
            run_id VARCHAR NOT NULL,
            loaded_at TIMESTAMP NOT NULL
        )''')
        # Comprobar cambios de archivos antes de comenzar la transacción.
        for spec in TABLE_SPECS:
            current = source_manifest[spec.name]
            row = con.execute(
                'SELECT source_sha256, row_count, columns_json FROM audit.raw_load_manifest WHERE table_name = ?',
                [spec.name],
            ).fetchone()
            if row is not None:
                changed = (row[0] != current['sha256'] or
                           row[1] != current['records_count'] or
                           json.loads(row[2]) != current['columns'])
                if changed and not force_reload:
                    raise RuntimeError(
                        f'La fuente {spec.filename} cambió desde la última carga; '
                        'investiga el cambio y usa --force-reload sólo si es intencional.'
                    )

        con.execute('BEGIN TRANSACTION')
        for spec in TABLE_SPECS:
            entry = source_manifest[spec.name]
            csv_file = input_dir / spec.filename
            table = 'raw.' + qi(spec.name)
            previous = con.execute(
                'SELECT source_sha256 FROM audit.raw_load_manifest WHERE table_name = ?',
                [spec.name],
            ).fetchone()
            existed = bool(con.execute(
                'SELECT 1 FROM information_schema.tables WHERE table_schema = ? AND table_name = ?',
                ['raw', spec.name],
            ).fetchone())
            if previous is not None and previous[0] == entry['sha256'] and existed and not force_reload:
                observed = con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                if observed == entry['records_count']:
                    action = 'SKIP_SAME_SOURCE'
                    outcomes.append({'table': spec.name, 'file': spec.filename, 'rows': observed,
                                     'columns': len(entry['columns']), 'sha256': entry['sha256'], 'action': action})
                    continue

            # Comprobar hash justo antes y justo después, por si cambiase en medio de lectura.
            if csv_inventory(csv_file)['sha256'] != entry['sha256']:
                raise RuntimeError(f'CSV modificado durante la preparación: {spec.filename}')
            con.execute(f'CREATE OR REPLACE TABLE {table} AS '
                        f'SELECT * FROM {csv_read_query(csv_file, entry["columns"])}')
            observed = con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            if observed != entry['records_count']:
                raise ValueError(f'Conteo diferente para {spec.name}: CSV={entry["records_count"]}, RAW={observed}')
            raw_columns = [x[0] for x in con.execute(f'DESCRIBE {table}').fetchall()]
            if raw_columns != entry['columns']:
                raise ValueError(f'Encabezados diferentes en RAW.{spec.name}')
            raw_types = [x[1] for x in con.execute(f'DESCRIBE {table}').fetchall()]
            if any(x.upper() != 'VARCHAR' for x in raw_types):
                raise ValueError(f'RAW.{spec.name} tiene tipos diferentes de VARCHAR')
            if csv_inventory(csv_file)['sha256'] != entry['sha256']:
                raise RuntimeError(f'CSV modificado durante la carga: {spec.filename}')
            con.execute('DELETE FROM audit.raw_load_manifest WHERE table_name = ?', [spec.name])
            con.execute('''INSERT INTO audit.raw_load_manifest
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                [spec.name, spec.filename, entry['sha256'], observed, len(entry['columns']),
                 json.dumps(entry['columns'], ensure_ascii=False), run_id,
                 datetime.now(timezone.utc).replace(tzinfo=None)])
            outcomes.append({'table': spec.name, 'file': spec.filename, 'rows': observed,
                             'columns': len(entry['columns']), 'sha256': entry['sha256'], 'action': 'LOADED'})
        con.execute('COMMIT')
        # Sólo se reporta éxito después de commit.
        if input_dir == DATA_RAW_DIR and db_path == DB_PATH:
            QUALITY_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
            (QUALITY_REPORTS_DIR / 'raw_load_report.json').write_text(
                json.dumps({'run_id': run_id, 'tables': outcomes}, indent=2, ensure_ascii=False),
                encoding='utf-8',
            )
        return outcomes
    except Exception:
        try:
            con.execute('ROLLBACK')
        except Exception:
            pass
        raise
    finally:
        con.close()


def main():
    parser = argparse.ArgumentParser(description='Fase 2: cargar CSV Olist sin convertirlos a RAW')
    parser.add_argument('--force-reload', action='store_true', help='Autoriza recarga deliberada si cambia CSV')
    arguments = parser.parse_args()
    result = load_raw(force_reload=arguments.force_reload)
    for entry in result:
        print(f"[{entry['action']}] raw.{entry['table']}: {entry['rows']} registros; {entry['columns']} columnas")
    print('[OK] CSV -> RAW verificado; los CSV no fueron modificados.')
    print('[INFO] CLEAN sigue vacío; falta diagnóstico y 15 reglas de limpieza.')


if __name__ == '__main__':
    main()
