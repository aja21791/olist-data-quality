"""Fase 1: inspección no destructiva de archivos CSV del dataset Olist.

No crea tablas ni transforma datos. El objetivo es identificar las fuentes
ANTES de asignar granularidad, tipos SQL y políticas de corrección.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

from .config import DATA_RAW_DIR, EXPECTED_CSV_FILES, QUALITY_REPORTS_DIR


def csv_inventory(csv_path: Path) -> dict:
    """Inspeccionar encabezados, filas, ancho y hash sin cambiar el origen."""
    if not csv_path.is_file() or csv_path.suffix.lower() != ".csv":
        raise ValueError(f"No es un CSV existente: {csv_path}")

    # newline="" permite que csv.reader procese correctamente campos multilínea.
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header is None:
            raise ValueError(f"Archivo CSV vacío: {csv_path.name}")
        if len(header) != len(set(header)):
            raise ValueError(f"Encabezados duplicados: {csv_path.name}")
        if any(not column.strip() for column in header):
            raise ValueError(f"Encabezado vacío: {csv_path.name}")

        row_count = 0
        width_mismatch = 0
        bad_row_examples = []
        for row in reader:
            row_count += 1
            if len(row) != len(header):
                width_mismatch += 1
                if len(bad_row_examples) < 5:
                    bad_row_examples.append(row_count + 1)

    sha256 = hashlib.sha256()
    with csv_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha256.update(chunk)

    return {
        "file": csv_path.name,
        "size_bytes": csv_path.stat().st_size,
        "columns_count": len(header),
        "columns": header,
        "records_count": row_count,
        "row_width_mismatches": width_mismatch,
        "bad_csv_record_numbers": bad_row_examples,
        "sha256": sha256.hexdigest(),
    }


def build_inventory(
    input_dir: Path = DATA_RAW_DIR,
    output_dir: Path = QUALITY_REPORTS_DIR,
    require_all_expected: bool = True,
) -> list[dict]:
    """Inspeccionar todos los CSV y crear inventario en JSON/CSV.

    Si falta algún CSV esperado, se detiene sin publicar un inventario parcial.
    """
    if not input_dir.is_dir():
        raise FileNotFoundError(f"No existe la carpeta: {input_dir}")
    csv_files = sorted(input_dir.glob("*.csv"), key=lambda p: p.name)
    if not csv_files:
        raise FileNotFoundError(
            f"No se encontraron archivos CSV en {input_dir}. Descarga Olist primero."
        )
    if require_all_expected:
        actual = {p.name for p in csv_files}
        missing = sorted(set(EXPECTED_CSV_FILES) - actual)
        if missing:
            raise FileNotFoundError(
                "Faltan archivos del dataset Olist: " + ", ".join(missing)
            )
    results = [csv_inventory(path) for path in csv_files]
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "source_inventory.json"
    csv_path = output_dir / "source_inventory.csv"
    with json_path.open("w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2, ensure_ascii=False)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "file", "size_bytes", "columns_count", "records_count",
            "row_width_mismatches", "sha256", "columns",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for item in results:
            writer.writerow({
                **{key: item[key] for key in fieldnames if key != "columns"},
                "columns": " | ".join(item["columns"]),
            })
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspeccionar CSV originales sin alterarlos")
    parser.add_argument("--allow-partial", action="store_true",
                        help="Permitir ejecutar con una muestra de CSV (no apto para ETL final)")
    arguments = parser.parse_args()
    try:
        results = build_inventory(require_all_expected=not arguments.allow_partial)
    except (FileNotFoundError, ValueError, UnicodeError) as exc:
        parser.exit(1, f"[ERROR] {exc}\n")
    print("Archivo | Registros | Columnas | Filas con ancho distinto")
    for item in results:
        print(f"{item['file']} | {item['records_count']} | "
              f"{item['columns_count']} | {item['row_width_mismatches']}")
    print("[OK] Reportes generados en reports/quality_reports/")


if __name__ == "__main__":
    main()
