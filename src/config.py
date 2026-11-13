"""Rutas centralizadas: independientes del directorio de ejecución."""
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_RAW_DIR = ROOT_DIR / "data" / "raw"
DATA_CLEAN_DIR = ROOT_DIR / "data" / "clean"
DATA_QUARANTINE_DIR = ROOT_DIR / "data" / "quarantine"
DB_DIR = ROOT_DIR / "database"
DB_PATH = DB_DIR / "ecommerce.duckdb"
REPORTS_DIR = ROOT_DIR / "reports"
QUALITY_REPORTS_DIR = REPORTS_DIR / "quality_reports"
FIGURES_DIR = REPORTS_DIR / "figures"
DICTIONARY_DIR = REPORTS_DIR / "data_dictionary"

# Inventario de archivos del dataset publicado por Olist en Kaggle.
# No representa un supuesto sobre la cantidad de filas ni sus tipos.
EXPECTED_CSV_FILES = (
    "olist_customers_dataset.csv",
    "olist_geolocation_dataset.csv",
    "olist_order_items_dataset.csv",
    "olist_order_payments_dataset.csv",
    "olist_order_reviews_dataset.csv",
    "olist_orders_dataset.csv",
    "olist_products_dataset.csv",
    "olist_sellers_dataset.csv",
    "product_category_name_translation.csv",
)


def ensure_directories() -> None:
    """Crear sólo rutas de proyecto, nunca alterar CSV existentes."""
    for directory in (
        DATA_RAW_DIR, DATA_CLEAN_DIR, DATA_QUARANTINE_DIR,
        DB_DIR, QUALITY_REPORTS_DIR, FIGURES_DIR, DICTIONARY_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)
