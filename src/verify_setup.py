"""Fase 1: comprobaciones del entorno, dependencias y conexión persistente."""
from __future__ import annotations

import argparse
import importlib.metadata
import sys
from pathlib import Path

from .config import DATA_RAW_DIR, DB_PATH, EXPECTED_CSV_FILES, ROOT_DIR
from .bootstrap import bootstrap

PACKAGES = ("duckdb", "pandas", "pyarrow", "matplotlib", "ipykernel", "kaggle")


def check_setup(require_data: bool = False) -> bool:
    print(f"Directorio de proyecto: {ROOT_DIR}")
    print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    ok = sys.version_info >= (3, 11)
    if not ok:
        print("[ERROR] Se necesita Python 3.11 o superior; se recomienda 3.12")
    for package in PACKAGES:
        try:
            version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            print(f"[ERROR] No se encontró la dependencia {package}")
            ok = False
        else:
            print(f"[OK] {package}: {version}")

    if ok:
        try:
            print(f"[OK] DuckDB conectado: {bootstrap()}")
        except Exception as exc:
            print(f"[ERROR] Falló la conexión persistente: {exc}")
            ok = False

    if require_data:
        missing = [name for name in EXPECTED_CSV_FILES
                   if not (DATA_RAW_DIR / name).is_file()]
        if missing:
            print("[ERROR] Faltan CSV originales:")
            for name in missing:
                print("   -", name)
            ok = False
        else:
            print(f"[OK] Se localizaron los {len(EXPECTED_CSV_FILES)} CSV esperados")

    if DB_PATH.exists():
        print(f"[OK] Existe database/ecommerce.duckdb ({DB_PATH.stat().st_size} bytes)")
    print("RESULTADO:", "PASS" if ok else "FAIL")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Comprobar la instalación del proyecto")
    parser.add_argument("--require-data", action="store_true")
    args = parser.parse_args()
    raise SystemExit(0 if check_setup(require_data=args.require_data) else 1)


if __name__ == "__main__":
    main()
