# Olist Data Quality — SQL + DuckDB + Python

Proyecto educativo de ingeniería y calidad de datos para Windows 11. **Estado del código: Fases 1, 2 y 3 (preparación, extracción a RAW y diagnóstico integral inicial).** Este repositorio **no afirma** tener aún las 15 reglas implementadas ni el conjunto Olist procesado. Los CSV originales se descargan desde Kaggle y se excluyen del control de versiones.

## Objetivo y alcance

Construir progresivamente un flujo ETL con trazabilidad, consultas analíticas SQL y pruebas automatizadas. Las dos únicas capas de datos persistentes serán `raw` y `clean` en `database/ecommerce.duckdb`. `audit` será únicamente un esquema auxiliar de control.

```text
CSV originales -> Extracción Python + DuckDB -> RAW
  -> 15 reglas (SQL + coordinación Python) -> CLEAN
  -> Pruebas críticas -> Parquet + reporte de calidad
```

No hay capa persistente STAGING. Las futuras etapas de transformación podrán utilizar CTE, vistas temporales o tablas temporales sin alterar el recorrido.

## Guía paso a paso

**Fase 1:** [`docs/FASE_1_TUTORIAL.md`](docs/FASE_1_TUTORIAL.md) (instalación de Windows 11). **Fase 2:** [`docs/FASE_2_TUTORIAL.md`](docs/FASE_2_TUTORIAL.md) (comprensión de datos y carga RAW). **Fase 3:** [`docs/FASE_3_TUTORIAL.md`](docs/FASE_3_TUTORIAL.md) (diagnóstico integral, reporte inicial y gráficos).

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
python -m src.extract            # Fase 2: CSV -> RAW, sin limpiar
python -m src.understand         # Fase 2: diccionario + exploración de RAW
python -m src.diagnose           # Fase 3: diagnóstico integral, CSV + gráficos + reporte inicial
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
| `src/model_metadata.py` | Hipótesis de claves, relaciones y granularidad que se verifican contra RAW |
| `src/extract.py` | Carga transaccional, idempotente, a RAW sin modificar CSV |
| `src/understand.py` | Exploración inicial de claves, referencias, vacíos, tipos, fechas y montos de RAW |
| `src/diagnose.py` | Fase 3: diagnóstico integral de 15 temas, solo lectura y generación del reporte inicial |
| `notebooks/01_data_quality_etl.ipynb` | Notebook educativo con Fases 1, 2 y 3; ejecutable en orden |
| `sql/profiling/` | Consultas SQL de Fases 2 y 3 (duplicados, IQR, fechas, conciliación, estados, postales) |
| `sql/transformations/` | 15 reglas SQL, por construir |
| `sql/validations/` | Pruebas SQL sobre CLEAN, por construir |
| `reports/quality_reports/` | Inventario de fuentes ahora; métricas comparativas después |
| `reports/data_dictionary/` | Diccionario y ER generados desde RAW en Fase 2 |
| `tests/` | Pruebas automatizadas de los componentes ya implementados |
| `docs/architecture.mmd` | Diagrama Mermaid del pipeline obligatorio |

## Reglas previstas para el pipeline completo

01 Duplicados · 02 Nulos · 03 Texto · 04 Conversión de tipos · 05 Fechas · 06 Precios · 07 Outliers · 08 Categorías · 09 Integridad referencial · 10 Estados del pedido · 11 Prefijos postales · 12 Pagos · 13 Métodos de pago · 14 Cadenas vacías · 15 Validación final.

Cada regla tendrá diagnóstico de RAW, política documentada, SQL de transformación hacia CLEAN, auditoría, prueba y análisis de excepciones. **Todavía no se consideran implementadas por este paquete de Fase 2.**

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

El paquete incluye el **código de Fases 1, 2 y 3** para cargar RAW, generar diccionario y diagnósticos integrales de calidad. Su ejecución sobre Olist real **depende de los CSV que descargues localmente**. No se han generado aquí cifras del dataset ni se afirma que tu base local esté cargada. El reporte de Fase 3 es el diagnóstico INICIAL, no el reporte final de calidad. CLEAN sigue pendiente, no se aplican todavía las 15 reglas y no se publican Parquet. Los scripts y la prueba de carga con datos sintéticos deben ejecutarse en tu entorno DuckDB.


## Fase 3: entregables del diagnóstico inicial

Después de `python -m src.diagnose`, revisar `reports/quality_reports/phase3_quality_report.md`, `phase3_issues.csv`, `phase3_kpis.csv`, `phase3_payment_summary.csv`, `phase3_outliers.csv`, `phase3_references.csv` y gráficos `reports/figures/phase3_*.png`. Cada tabla de métricas queda también en `phase3_*.csv`; `phase3_summary.json` consolida el resultado. No hay tablas modificadas ni tercera capa de datos.

Si se produce un error gráfico, ejecutar `python -m src.diagnose --no-figures`. Las pruebas de Fase 3 usan datos sintéticos y no requieren Olist real.
