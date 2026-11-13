"""Fase 1: crear una BD DuckDB persistente y reservar esquemas.

IMPORTANTE: las tablas RAW/CLEAN se crean en fases posteriores.
No hay un esquema ni capa de datos persistente STAGING.
"""
from .config import DB_PATH, ensure_directories


def bootstrap() -> str:
    """Inicializar la base (repetible, sin borrar registros existentes)."""
    try:
        import duckdb
    except ImportError as exc:
        raise RuntimeError(
            "Falta DuckDB. Activa 'olist-quality' y ejecuta conda env create -f environment.yml"
        ) from exc

    ensure_directories()
    connection = duckdb.connect(str(DB_PATH))
    try:
        # RAW y CLEAN son las únicas capas persistentes de datos.
        # audit será sólo un esquema de control/bitácoras.
        for schema in ("raw", "clean", "audit"):
            connection.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        result = connection.execute("SELECT 42 AS healthcheck").fetchone()[0]
        if result != 42:
            raise RuntimeError("La consulta de control de DuckDB falló")
    finally:
        connection.close()
    return str(DB_PATH)


def main() -> None:
    db_path = bootstrap()
    print(f"[OK] Base de datos persistente disponible: {db_path}")
    print("[OK] Schemas: raw, clean (datos) y audit (control)")
    print("[INFO] Aún no se han cargado los CSV a tablas RAW.")


if __name__ == "__main__":
    main()
