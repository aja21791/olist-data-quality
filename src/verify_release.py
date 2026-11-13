"""Verificación independiente de una publicación Parquet terminada.

No modifica RAW, CLEAN ni el contenido publicado. Contrasta conteos,
esquemas, tamaños y SHA-256 frente al manifiesto de Fase 7.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_CLEAN_DIR, QUALITY_REPORTS_DIR
from .release import RELEASE_FILE, TEMPORAL_SIDECAR, file_sha256
from .transform_schema import SCHEMA


def verify_release(release_dir=DATA_CLEAN_DIR, report_dir=QUALITY_REPORTS_DIR) -> dict:
    import duckdb
    folder = Path(release_dir)
    manifest_file = folder / RELEASE_FILE
    if not manifest_file.is_file():
        raise FileNotFoundError(f'No existe el manifiesto de publicación: {manifest_file}')
    manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
    if manifest.get('status') != 'PUBLISHED':
        raise ValueError('El manifiesto no identifica un lote publicado')
    rows = manifest.get('tables')
    if not isinstance(rows, list) or len(rows) != len(SCHEMA):
        raise ValueError('Se esperaban exactamente nueve tablas en el manifiesto')
    by_name = {entry['table']:entry for entry in rows}
    if set(by_name) != set(SCHEMA):
        raise ValueError('Las nueve tablas de Parquet no coinciden con el contrato')
    actual_parquets = set(path.name for path in folder.glob('*.parquet'))
    expected_parquets = {f'{table}.parquet' for table in SCHEMA}
    if actual_parquets != expected_parquets:
        raise ValueError(f'Parquet ausentes o inesperados: {sorted(actual_parquets ^ expected_parquets)}')
    verified = []
    with duckdb.connect() as con:
        for table in SCHEMA:
            metadata = by_name[table]
            if metadata['file'] != f'{table}.parquet':
                raise ValueError(f'Ruta inesperada de archivo para tabla {table}')
            path = folder / metadata['file']
            if not path.is_file() or path.stat().st_size != int(metadata['bytes']):
                raise ValueError(f'{table}: tamaño o archivo inválido')
            if file_sha256(path) != metadata['sha256']:
                raise ValueError(f'{table}: archivo modificado (SHA-256 incorrecta)')
            path_literal = "'" + path.resolve().as_posix().replace("'", "''") + "'"
            count = int(con.execute(f'SELECT COUNT(*) FROM read_parquet({path_literal})').fetchone()[0])
            schema = [list(r[:2]) for r in con.execute(
                f'DESCRIBE SELECT * FROM read_parquet({path_literal})').fetchall()]
            if count != int(metadata['parquet_rows']) or count != int(metadata['clean_rows']):
                raise ValueError(f'{table}: conteo no coincide con el manifiesto')
            if schema != metadata['schema']:
                raise ValueError(f'{table}: esquema no coincide con el manifiesto')
            if [column[0] for column in schema] != SCHEMA[table]:
                raise ValueError(f'{table}: orden de columnas incompatible con el contrato')
            verified.append({'table':table, 'rows':count, 'sha256':metadata['sha256']})
    companion = manifest.get('temporal_policy')
    if not isinstance(companion,dict) or companion.get('file') != TEMPORAL_SIDECAR:
        raise ValueError('Falta referencia de trazabilidad R05 en manifiesto')
    sidecar = folder / TEMPORAL_SIDECAR
    if not sidecar.is_file() or sidecar.stat().st_size != int(companion['bytes']):
        raise ValueError('Archivo auxiliar temporal ausente o con tamaño incorrecto')
    if file_sha256(sidecar) != companion['sha256']:
        raise ValueError('Archivo auxiliar temporal modificado (SHA-256 incorrecta)')
    with sidecar.open('r',encoding='utf-8-sig',newline='') as file:
        data = list(csv.DictReader(file))
    if len(data) != int(companion['events']):
        raise ValueError('Las anomalías temporales no coinciden con el manifiesto')
    outcome = {'status':'VERIFIED','release_id':manifest['release_id'],
               'verified_at_utc':datetime.now(timezone.utc).isoformat(),
               'parquet_tables':len(verified), 'tables':verified,
               'temporal_policy_events':len(data)}
    report_dir=Path(report_dir)
    report_dir.mkdir(parents=True,exist_ok=True)
    (report_dir/'phase7_verification_summary.json').write_text(
        json.dumps(outcome,ensure_ascii=False,indent=2),encoding='utf-8')
    return outcome


def main():
    parser=argparse.ArgumentParser(description='Verificar publicación Parquet a partir del manifiesto')
    parser.add_argument('--output',type=Path,default=DATA_CLEAN_DIR)
    parser.add_argument('--reports',type=Path,default=QUALITY_REPORTS_DIR)
    args=parser.parse_args()
    try:
        outcome=verify_release(args.output,args.reports)
    except Exception as exc:
        print('[VERIFICATION FAILED]',exc)
        raise SystemExit(1) from exc
    print(f"[VERIFIED] {outcome['parquet_tables']} Parquet, "
          f"{outcome['temporal_policy_events']} eventos R05; release={outcome['release_id']}")


if __name__=='__main__':
    main()
