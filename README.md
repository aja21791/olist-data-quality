# Olist Data Quality — SQL + DuckDB + Python

Proyecto educativo de ingeniería y calidad de datos para Windows 11. **Estado actual: Fase 1 (preparación).** Este repositorio **no afirma** tener aún las 15 reglas implementadas ni el conjunto Olist procesado. Los CSV originales se descargan desde Kaggle y se excluyen del control de versiones.

## Objetivo y alcance

Construir progresivamente un flujo ETL con trazabilidad, consultas analíticas SQL y pruebas automatizadas. Las dos únicas capas de datos persistentes serán `raw` y `clean` en `database/ecommerce.duckdb`. `audit` será únicamente un esquema auxiliar de control.

```text
CSV originales -> Extracción Python + DuckDB -> RAW
  -> 15 reglas (SQL + coordinación Python) -> CLEAN
  -> Pruebas críticas -> Parquet + reporte de calidad
```

No hay capa persistente STAGING. Las futuras etapas de transformación podrán utilizar CTE, vistas temporales o tablas temporales sin alterar el recorrido.

## Guía paso a paso

**Leer primero:** [`docs/FASE_1_TUTORIAL.md`](docs/FASE_1_TUTORIAL.md). Incluye nueve pasos guiados, comprobaciones, errores frecuentes y comandos exactos para Windows 11.

### Inicio rápido (Miniconda Prompt, desde la carpeta del proyecto)

```bat
conda env create -f environment.yml
conda activate olist-quality
python -m ipykernel install --user --name olist-quality --display-name "Python (olist-quality)"
python -m src.verify_setup
python -m src.bootstrap
kaggle auth login
kaggle datasets download olistbr/brazilian-ecommerce -p data/raw --unzip
python -m src.source_inventory
python -m src.verify_setup --require-data
python -m pytest -q tests
```

Si los archivos CSV ya fueron descargados mediante navegador, copiarlos a `data/raw/` **sin abrirlos ni guardarlos con Excel** y omitir el comando de Kaggle.

Abrir VS Code con `code .`; seleccionar el intérprete `olist-quality` y el kernel de Jupyter `Python (olist-quality)`.

## Directorios

| Ruta | Propósito |
|---|---|
| `data/raw/` | Los 9 CSV originales, sin modificaciones |
| `data/clean/` | Parquet aprobados (fases posteriores) |
| `data/quarantine/` | Anomalías justificadamente retenidas (fase posterior) |
| `database/ecommerce.duckdb` | Base persistente con esquemas raw, clean y audit |
| `src/config.py` | Rutas y catálogo de archivos fuente |
| `src/bootstrap.py` | Inicializar base y esquemas sin borrar datos |
| `src/source_inventory.py` | Perfilado inicial: cabeceras, conteos, problemas estructurales, SHA-256 |
| `src/verify_setup.py` | Validación de instalación y archivos |
| `notebooks/01_data_quality_etl.ipynb` | Notebook educativo, iniciado en Fase 1 |
| `sql/profiling/` | Consultas SQL de diagnóstico, por construir |
| `sql/transformations/` | 15 reglas SQL, por construir |
| `sql/validations/` | Pruebas SQL sobre CLEAN, por construir |
| `reports/quality_reports/` | Inventario de fuentes ahora; métricas comparativas después |
| `reports/data_dictionary/` | Diccionario derivado de datos reales, por construir |
| `tests/` | Pruebas automatizadas de los componentes ya implementados |
| `docs/architecture.mmd` | Diagrama Mermaid del pipeline obligatorio |

## Reglas previstas para el pipeline completo

01 Duplicados · 02 Nulos · 03 Texto · 04 Conversión de tipos · 05 Fechas · 06 Precios · 07 Outliers · 08 Categorías · 09 Integridad referencial · 10 Estados del pedido · 11 Prefijos postales · 12 Pagos · 13 Métodos de pago · 14 Cadenas vacías · 15 Validación final.

Cada regla tendrá diagnóstico de RAW, política documentada, SQL de transformación hacia CLEAN, auditoría, prueba y análisis de excepciones. **Todavía no se consideran implementadas por este paquete de Fase 1.**

## Dataset

- Fuente oficial: https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
- Período declarado por la fuente: 2016–2018.
- Nueve CSV esperados: ver `src/config.py`.
- Las columnas y los tipos originales no se anticipan; `src/source_inventory.py` los inspecciona sobre los CSV realmente descargados.
- No suponer duplicados por `order_id` en artículos o pagos; tampoco unicidad de prefijos postales geográficos.

## Calidad y reproducibilidad

- `python -m src.source_inventory` verifica el ancho de las filas e identifica anomalías estructurales. Exporta `reports/quality_reports/source_inventory.json` y `.csv`.
- `python -m pytest -q tests` prueba con **datos sintéticos** los mecanismos de lectura y conexión, sin atribuir esos resultados a Olist.
- `python -m src.bootstrap` puede ejecutarse varias veces sin eliminar registros.
- El archivo `.gitignore` excluye los datos y la base persistente de Git; **no reemplaza una política de respaldo**.
- Las descargas y métricas reales requieren internet, dependencias instaladas y la ejecución local del dataset en Windows.

## Limitaciones de esta entrega

La Fase 1 habilita el entorno, la base y el inventario, pero **no** carga tablas RAW, no construye CLEAN, no aplica las 15 reglas y no publica Parquet. Esto evita representar como completado un pipeline que todavía no ha procesado datos reales. El diseño del repositorio conserva las rutas de los entregables posteriores.
