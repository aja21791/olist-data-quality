# Olist Data Quality — SQL + DuckDB + Python

Proyecto educativo de ingeniería y calidad de datos para Windows 11. **Estado del código: Fases 1 a 7, con política R05 de timestamps logísticos no verificables y publicación Parquet condicionada a validaciones críticas.** Las quince reglas están implementadas como SQL y orquestación Python. La ejecución de las pruebas sobre los CSV reales debe verificarse localmente; los reportes suministrados se generan desde la base de cada usuario. Los CSV originales se descargan desde Kaggle y se excluyen del control de versiones.

## Actualización R05 — Cierre técnico Fase 7

La política `R05_CARRIER_TIMESTAMP_UNVERIFIABLE_V1` mantiene el histórico de fechas en RAW y AUDIT, y sustituye con `NULL` en CLEAN sólo las fechas de transportista involucradas en cronologías contradictorias. La vista `clean.orders_temporal_quality` diferencia los `NULL` de origen de los causados por la política. **No se inventan fechas ni se marcan las 189 anomalías originales como aprobadas**. La validación mantiene un control crítico de correspondencia RAW/CLEAN/AUDIT y publica la cifra original como advertencia.

Consulta **[el tutorial de cierre de Fase 7](docs/FASE_7_CIERRE_POLITICA_TEMPORAL.md)** antes de ejecutar:

```powershell
conda activate olist-quality
python -m pytest -q tests
python -m src.transform
python -m src.validate
python -m src.release
python -m src.release --publish
python -m src.verify_release
```

Cuando el gate aprueba, la salida incluye los **nueve Parquet**, `release_manifest.json`, `quality_report.md` y el CSV auxiliar `temporal_quality_exceptions.csv`. Los datos reales se verifican en tu entorno; no se incluyen en el código distribuido.

## Objetivo y alcance

Construir progresivamente un flujo ETL con trazabilidad, consultas analíticas SQL y pruebas automatizadas. Las dos únicas capas de datos persistentes serán `raw` y `clean` en `database/ecommerce.duckdb`. `audit` será únicamente un esquema auxiliar de control.

```text
CSV originales -> Extracción Python + DuckDB -> RAW
  -> 15 reglas (SQL + coordinación Python) -> CLEAN
  -> Pruebas críticas -> Parquet + reporte de calidad
```

No hay capa persistente STAGING. Las futuras etapas de transformación podrán utilizar CTE, vistas temporales o tablas temporales sin alterar el recorrido.

## Guía paso a paso

**Fase 1:** [`docs/FASE_1_TUTORIAL.md`](docs/FASE_1_TUTORIAL.md) (instalación de Windows 11). **Fase 2:** [`docs/FASE_2_TUTORIAL.md`](docs/FASE_2_TUTORIAL.md) (comprensión de datos y carga RAW). **Fase 3:** [`docs/FASE_3_TUTORIAL.md`](docs/FASE_3_TUTORIAL.md) (diagnóstico integral, reporte inicial y gráficos). **Fase 4:** [`docs/FASE_4_TUTORIAL.md`](docs/FASE_4_TUTORIAL.md) (15 reglas, CLEAN, cuarentena y auditoría). **Fase 5:** [`docs/FASE_5_TUTORIAL.md`](docs/FASE_5_TUTORIAL.md) (validación formal, severidades, bloqueo y auditoría).

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
python -m src.transform          # Fase 4: 15 reglas RAW -> CLEAN, SIN publicar Parquet
python -m src.validate           # Fase 5: pruebas formales, bloquea con FAIL crítico
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
| `src/transform.py` | Fase 4: ejecuta reglas SQL y construye CLEAN bajo una transacción |
| `src/transform_schema.py` | Metadatos de tipos y claves verificados contra RAW |
| `src/validate.py` | Fase 5: controles SQL, gate PASS/WARNING/FAIL, auditoría e informes |
| `notebooks/01_data_quality_etl.ipynb` | Notebook educativo con Fases 1 a 5; ejecutable en orden |
| `sql/profiling/` | Consultas SQL de Fases 2 y 3 (duplicados, IQR, fechas, conciliación, estados, postales) |
| `sql/transformations/tables/` | 9 transformaciones SQL de RAW a CLEAN |
| `sql/transformations/rules/` | 15 consultas SQL reales de auditoría/calidad |
| `sql/validations/` | Pruebas SQL versionadas sobre CLEAN (Fase 5) |
| `reports/quality_reports/` | Inventario de fuentes ahora; métricas comparativas después |
| `reports/data_dictionary/` | Diccionario y ER generados desde RAW en Fase 2 |
| `tests/` | Pruebas automatizadas de los componentes ya implementados |
| `docs/architecture.mmd` | Diagrama Mermaid del pipeline obligatorio |

## Reglas previstas para el pipeline completo

01 Duplicados · 02 Nulos · 03 Texto · 04 Conversión de tipos · 05 Fechas · 06 Precios · 07 Outliers · 08 Categorías · 09 Integridad referencial · 10 Estados del pedido · 11 Prefijos postales · 12 Pagos · 13 Métodos de pago · 14 Cadenas vacías · 15 Validación final.

Cada regla tendrá diagnóstico de RAW, política documentada, SQL de transformación hacia CLEAN, auditoría, prueba y análisis de excepciones. **Implementadas en Fase 4; consulta el tutorial para políticas y validaciones.**

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

El paquete incluye el **código de Fases 1, 2 y 3** para cargar RAW, generar diccionario y diagnósticos integrales de calidad. Su ejecución sobre Olist real **depende de los CSV que descargues localmente**. No se han generado aquí cifras del dataset ni se afirma que tu base local esté cargada. El reporte de Fase 3 es el diagnóstico INICIAL, no el reporte final de calidad. CLEAN puede construirse con `src.transform` usando SQL; no hay Parquet ni reporte final hasta superar las pruebas formales y la fase de publicación. Los scripts y la prueba de carga con datos sintéticos deben ejecutarse en tu entorno DuckDB.


## Fase 3: entregables del diagnóstico inicial

Después de `python -m src.diagnose`, revisar `reports/quality_reports/phase3_quality_report.md`, `phase3_issues.csv`, `phase3_kpis.csv`, `phase3_payment_summary.csv`, `phase3_outliers.csv`, `phase3_references.csv` y gráficos `reports/figures/phase3_*.png`. Cada tabla de métricas queda también en `phase3_*.csv`; `phase3_summary.json` consolida el resultado. No hay tablas modificadas ni tercera capa de datos.

Si se produce un error gráfico, ejecutar `python -m src.diagnose --no-figures`. Las pruebas de Fase 3 usan datos sintéticos y no requieren Olist real.


## Fase 4: uso y entregables

```powershell
conda activate olist-quality
python -m src.transform
python -m pytest -q tests
```

El proyecto reconstruye CLEAN transaccionalmente, sin tocar RAW, y conserva el historial de auditoría. Las tablas auxiliares temporales no crean STAGING persistente. Revisar:

- `reports/quality_reports/phase4_transformation_report.md`
- `reports/quality_reports/phase4_clean_overview.csv`
- `reports/quality_reports/phase4_rule_results.csv`
- `reports/quality_reports/phase4_quarantine_summary.csv`
- `audit.transform_runs`, `audit.rule_results`, `audit.quarantine_records`

**Advertencia de integridad:** se permiten excepciones no solucionables sin fuente externa y se registran como WARNING; la Fase 5 define reglas críticas de publicación. Las pruebas locales de DuckDB deben ejecutarse en el entorno del estudiante.


## Fase 5: validación formal de calidad (sin publicación Parquet)

```powershell
conda activate olist-quality
python -m src.validate
python -m pytest -q tests/test_phase5.py
python -m pytest -q tests
```

El validador utiliza `sql/validations/*.sql` y `src/transform_schema.py`; verifica tablas, tipos, unicidad, campos obligatorios, integridad referencial, cronología, valores monetarios, dominios, prefijos, estados y conciliación de pagos. La Fase 5 sólo **lee** `raw.*` y `clean.*`; añade resultados históricos a `audit.validation_runs` y `audit.validation_results`. Exporta informes en:

- `reports/quality_reports/phase5_validation_report.md`
- `reports/quality_reports/phase5_validation_results.csv`
- `reports/quality_reports/phase5_validation_summary.json`
- `reports/quality_reports/phase5_payment_exceptions.csv`

Los resultados son `PASS` (sin incumplimientos), `WARNING` (pendiente de revisión sin bloqueo) y `FAIL` (incumplimiento crítico). Con fallos críticos, el módulo devuelve código 1 y el estado `BLOCKED`. **No exporta Parquet; tampoco borra ni edita registros para conseguir PASS.** Los fallos críticos deben investigarse antes de la publicación de la Fase 7.

El tutorial completo explica cada paso: `docs/FASE_5_TUTORIAL.md`. El notebook integra las mismas funciones para evitar duplicar lógica. En este paquete no se incluyen resultados reales de Olist; deben calcularse en la base DuckDB local.

## Fase 6 — Orquestación y gestión verificable de excepciones

La secuencia integra Fases 2–5 en una ejecución: CSV originales → extracción Python + DuckDB → RAW → diagnóstico inicial → 15 reglas SQL → CLEAN → pruebas formales → conciliación de excepciones → dictamen. **No existe STAGING persistente ni se generan Parquet en esta fase.**

```powershell
conda activate olist-quality
python -m pytest -q tests/test_phase6.py
python -m src.pipeline --no-figures
# Revalidar un CSV de revisión SIN volver a importar ni transformar:
python -m src.pipeline --validate-only
```

Códigos de salida: `0` candidato evaluado sin bloqueo en la puerta de Fase 6 (aún sin publicar), `2` BLOCKED por calidad, `1` error técnico.

Los hallazgos reales aportados por el usuario para la Fase 5 (13/11/2026) fueron 166 eventos `carrier_before_purchase`, 23 `delivery_before_carrier`, siete advertencias y resultado original `BLOCKED`. **No se ha aprobado ninguna de las 189 anomalías**. Un registro de revisión se crea automáticamente con estado `PENDIENTE` si no existe, en `reports/quality_reports/phase5_temporal_review_register.csv`; si ya existe, nunca se sobrescribe. Se exige constancia documental, responsable, fecha ISO con zona horaria y coincidencia de los eventos actuales antes de contabilizar aprobaciones válidas.

La puerta de Fase 6 distingue los `FAIL` originales de Fase 5 del posible dictamen preparatorio `READY_FOR_PHASE7_WITH_EXCEPTIONS`. Este último no cambia el resultado original, no corrige timestamps y no implica que las fuentes externas se hayan verificado: solamente permitiría estudiar la publicación en Fase 7, donde todavía harán falta los controles finales.

Los registros de ejecución y decisiones se almacenan en `audit.pipeline_runs`, `audit.pipeline_steps` y `audit.temporal_review_assessments`. Las salidas están en `reports/quality_reports/phase6_*`. **Guía integral:** `docs/FASE_6_TUTORIAL.md`.


## Fase 7 — Publicación Parquet controlada

La publicación requiere que la Fase 5 y el gate de Fase 6 permitan continuar. **El estado real del dataset Olist analizado continúa `BLOCKED` por 189 eventos temporales sin aprobación**, por lo que el comando de exportación NO debe generar Parquet reales todavía.

```powershell
conda activate olist-quality
python -m pytest -q tests
python -m src.release                 # preflight, sin Parquet
python -m src.release --publish       # intento explícito: sigue bloqueado hasta autorización
python -m src.pipeline --no-figures --publish   # pipeline integral con publicación condicional
```

Tras una aprobación válida, el módulo exporta 9 Parquet ZSTD, compara filas y tipos leyendo cada uno, calcula SHA-256, crea `release_manifest.json` y el reporte `reports/quality_reports/phase7_quality_report.md`. En presencia de excepciones críticas aceptadas exige, además, una autorización manual de responsables de negocio y calidad vinculada a la huella de revisiones. Los CSV originales no se alteran.

**Sobre Windows:** el intercambio del directorio `data/clean` usa renombrados y rollback ante excepciones; una caída eléctrica entre renombrados no proporciona atomicidad absoluta. Consultar `docs/FASE_7_TUTORIAL.md` para límites, recuperación y detalles de la política.
