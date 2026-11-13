# Fase 2 — Comprensión del dataset Olist y extracción hacia RAW

**Objetivo.** Examinar la estructura real de los CSV originales, identificar granularidad, claves candidatas y relaciones, y realizar la **carga inicial de datos íntegros a RAW**. No se aplica ninguna de las 15 reglas de limpieza ni se escribe CLEAN.

**Ubicación de trabajo.** VS Code, terminal **PowerShell** con `olist-quality` activado, situada en la carpeta raíz `olist-data-quality`. Para el análisis visual, el notebook `notebooks/01_data_quality_etl.ipynb`. Para revisar SQL, `sql/profiling/01_raw_exploration.sql`.

La documentación del curso (Clase 4, fases de descubrimiento y preparación) enfatiza inventario, estructura, diccionario, verificación del origen, calidad y preparación previa al modelado. Nuestra implementación sigue ese orden.

## Paso 1: verificar las fuentes originales

**1. ¿Qué vamos a realizar?** Comprobar que los nueve CSV se descargaron y que el proyecto está en condiciones de cargarlos.

**2. ¿Por qué es necesario?** Un archivo ausente o mal formado hace que los resultados sean incompletos o que se pierdan datos.

**3. ¿Dónde debemos trabajar?** Terminal PowerShell de VS Code, en la raíz del proyecto.

**4. Implementación.**

```powershell
conda activate olist-quality
python -m src.verify_setup --require-data
python -m src.source_inventory
```

**5. Explicación del código.** El primer programa confirma dependencias y presencia de archivos. El segundo lee CSV sin modificarlos y calcula columnas, conteos, anomalías del ancho y SHA-256.

**6. Resultados esperados.** `reports/quality_reports/source_inventory.csv` y `source_inventory.json`. No se anticipan cantidades: el programa las obtiene de los CSV reales.

**7. Comprobación.** Cada archivo debe aparecer en el inventario. Revisar que `row_width_mismatches` sea cero. Un error estructural requiere investigación del CSV, no borrado automático de filas.

**8. Problemas frecuentes.** Si faltan CSV, descargar el dataset desde Kaggle y descomprimirlo en `data/raw/`. Si se encuentra un CSV adicional, moverlo fuera de `data/raw/` tras verificar que no pertenece al dataset. Si el comando `python` usa otro entorno, seleccionar `olist-quality` en VS Code.

**9. Resultado del paso.** Inventario reproducible de las fuentes, con su hash de integridad.

## Paso 2: aprender la estructura y las relaciones esperadas

**1. ¿Qué vamos a realizar?** Conocer el propósito y nivel de detalle de cada archivo, sin dar por válidas las claves todavía.

**2. ¿Por qué es necesario?** Un `order_id` repetido en líneas de artículos o pagos no equivale a un duplicado. `customer_unique_id` identifica al cliente a través de compras, mientras que `customer_id` identifica un registro de cliente asociado a una orden.

**3. ¿Dónde debemos trabajar?** `src/model_metadata.py` para consultar la propuesta de claves; notebook, sección Fase 2, para contrastarla con los CSV.

**4. Implementación.** Ver el catálogo de tablas `TABLE_SPECS` y las relaciones `RELATIONSHIPS` en el archivo indicado. Las candidatas principales son `orders(order_id)`, `customers(customer_id)`, `order_items(order_id,order_item_id)`, `order_payments(order_id,payment_sequential)`, `products(product_id)`, `sellers(seller_id)` y `category_translation(product_category_name)`. Las reseñas y la geolocalización no reciben una PK supuesta.

**5. Explicación del código.** El catálogo documenta **hipótesis de modelado**, no restricciones confirmadas. El programa verifica que las columnas existan y mide nulos, claves repetidas y claves huérfanas.

**6. Resultados esperados.** Un conjunto de claves candidatas cuya validez quedará explícita como `VALID_CANDIDATE`, `REVIEW` o `MISSING_COLUMNS` tras el perfilado.

**7. Comprobación.** Ver `raw_keys.csv` y `raw_references.csv` después del Paso 4. Un grupo de reseñas con `review_id` repetido requiere investigación antes de suponer que es erróneo.

**8. Problemas frecuentes.** Intentar que `order_id` sea único dentro de `order_items` elimina líneas válidas. No hacer esa transformación. No utilizar prefijos postales como una clave primaria.

**9. Resultado del paso.** Hipótesis explícitas y verificables de granularidad y relaciones.

## Paso 3: cargar las nueve tablas RAW

**1. ¿Qué vamos a realizar?** Extraer CSV originales y crear `raw.customers`, `raw.orders`, `raw.order_items`, `raw.order_payments`, `raw.order_reviews`, `raw.products`, `raw.sellers`, `raw.geolocation` y `raw.category_translation`.

**2. ¿Por qué es necesario?** RAW ofrece un punto de referencia persistente e inmutable durante la limpieza posterior. Permite reproducir y auditar las decisiones.

**3. ¿Dónde debemos trabajar?** Ejecutar `src/extract.py` desde PowerShell en la carpeta del proyecto.

**4. Implementación.**

```powershell
python -m src.extract
```

**5. Explicación del código.** Python inspecciona primero encabezados, filas y hashes; DuckDB ejecuta `CREATE OR REPLACE TABLE raw... AS SELECT * FROM read_csv(...)`. Todos los campos se declaran `VARCHAR`; las celdas vacías se conservan como `''`, sin imputación ni conversión prematura. Se comparan columnas y filas con el CSV. Una transacción evita cargas parciales, y `audit.raw_load_manifest` registra origen, hash, estructura, número de registros y ejecución. No se altera ningún CSV.

**6. Resultados esperados.** Nueve tablas RAW en `database/ecommerce.duckdb` y `reports/quality_reports/raw_load_report.json`. En primera ejecución cada tabla mostrará `LOADED` con su conteo **real**.

**7. Comprobación.** Ejecutar de nuevo `python -m src.extract`. Una fuente idéntica debe indicar `SKIP_SAME_SOURCE` y no duplicar registros. Si alguna fuente cambió, la ejecución se detiene para evitar sustituciones silenciosas; `--force-reload` **sólo** debe utilizarse tras investigar y aprobar expresamente el cambio.

**8. Problemas frecuentes.** `FileNotFoundError`: CSV en ubicación incorrecta. Error de lectura CSV: comprobar codificación y delimitador; no utilizar `ignore_errors`. `database locked`: cerrar conexiones de Jupyter/otros procesos. No borrar la base por un fallo sin revisar antes el mensaje. Un cambio de hash obliga a investigar la fuente.

**9. Resultado del paso.** Extracción verificable a RAW y manifiesto de auditoría.

## Paso 4: generar diagnóstico relacional y diccionario de datos

**1. ¿Qué vamos a realizar?** Calcular tamaños, tipos SQL, vacíos, unicidad, referencias, rangos temporales y monetarios, categorías y el diagrama ER.

**2. ¿Por qué es necesario?** Las reglas de limpieza deben responder a problemas realmente detectados. El perfilado inicial aporta la línea base para comparar con CLEAN al terminar el ETL.

**3. ¿Dónde debemos trabajar?** Ejecutar `src/understand.py` y abrir los resultados en `reports/`.

**4. Implementación.**

```powershell
python -m src.understand
```

**5. Explicación del código.** El programa consulta RAW usando SQL (nunca `UPDATE`, `DELETE` ni `ALTER` sobre RAW) y genera archivos CSV con resultados reales. Usa `GROUP BY`/`HAVING` para claves candidatas, `NOT EXISTS` para detectar huérfanos, `TRY_CAST` para diagnóstico de fechas y dinero. **No** convierte los datos originales. Las columnas inesperadas se registran en el diccionario, y las columnas esperadas que falten se reportan sin inventarlas.

**6. Resultados esperados.** Se crean:

- `reports/quality_reports/raw_tables.csv`
- `reports/quality_reports/raw_missingness.csv`
- `reports/quality_reports/raw_keys.csv`
- `reports/quality_reports/raw_references.csv`
- `reports/quality_reports/raw_dates.csv`
- `reports/quality_reports/raw_monetary.csv`
- `reports/quality_reports/raw_categories.csv`
- `reports/quality_reports/raw_profile_summary.json`
- `reports/data_dictionary/raw_columns.csv`
- `reports/data_dictionary/data_dictionary_initial.md`
- `reports/data_dictionary/olist_er.mmd`

**7. Comprobación.** Abrir `raw_tables.csv`, comparar conteos con `source_inventory.csv`, y leer `raw_keys.csv`. Revisar `raw_references.csv`: las relaciones con `NO_ORPHANS_OBSERVED` no presentaron huérfanos; con `REVIEW` deben investigarse. Las cardinalidades son hipótesis del modelo y se confrontan con las frecuencias; las referencias de traducción no son obligatorias.

**8. Problemas frecuentes.** Si aparece `Faltan tablas RAW`, volver al Paso 3. Si una PK candidata aparece `REVIEW`, **no** ejecutar `DROP DUPLICATES`: la siguiente fase decidirá si el caso es legítimo. Un vacío puede ser válido en una fecha dependiendo del estado del pedido. El archivo ER es conceptual y no impone constraints físicos.

**9. Resultado del paso.** Informe inicial para diseñar las 15 reglas sobre evidencias reales.

## Paso 5: ejecutar las pruebas y analizar el notebook

**1. ¿Qué vamos a realizar?** Comprobar idempotencia y tipos con datos sintéticos, después revisar resultados reales en tablas y gráficos.

**2. ¿Por qué es necesario?** Las pruebas confirman el mecanismo incluso cuando el dataset real contiene anomalías. Distinguir datos sintéticos de resultados Olist evita conclusiones falsas.

**3. ¿Dónde debemos trabajar?** Terminal de VS Code y `notebooks/01_data_quality_etl.ipynb`.

**4. Implementación.**

```powershell
python -m pytest -q tests
```

Luego abrir el notebook y ejecutar **Run All**, utilizando `Python (olist-quality)`. El notebook llama a los mismos módulos de Python; no hay una segunda implementación del ETL.

**5. Explicación del código.** Las pruebas construyen nueve CSV sintéticos temporales, verifican carga RAW, conservación de `001.20`, campos vacíos, múltiples líneas y reejecución sin duplicar. El notebook presenta métricas calculadas sobre Olist real una vez cargado.

**6. Resultados esperados.** Pruebas aprobadas en un entorno con DuckDB; tablas de perfilado y gráficas sobre los datos reales. Los números dependen de la descarga local; no se muestran cifras asumidas.

**7. Comprobación.** El manifiesto de auditoría, los CSV de calidad y el diccionario deberán existir. Si una prueba falla, investigar y resolver el defecto en el código antes de continuar.

**8. Problemas frecuentes.** `ModuleNotFoundError`: activar `olist-quality`. Notebook usa intérprete incorrecto: cambiar kernel en VS Code. Si no aparece el diagrama: abrir el `.mmd` con una extensión Mermaid o copiarlo a un visor Mermaid compatible.

**9. Resultado del paso.** Fase 2 lista para revisión; las 15 transformaciones de RAW a CLEAN **todavía no están desarrolladas**.

## Interpretación y cierre

En esta fase, los campos del CSV se almacenan como `VARCHAR`: esta es la representación en RAW, **no** el tipo analítico definitivo. Los tipos finales (`TIMESTAMP`, `DECIMAL`, etc.) se decidirán en CLEAN con `TRY_CAST` y políticas documentadas.

- **PK**: identificador único de cada fila en una tabla, cuando su unicidad está confirmada.
- **FK**: referencia a otra tabla; los CSV no imponen constraints, por lo que las comprobamos mediante SQL.
- **Granularidad**: qué representa exactamente una fila. Es el concepto que impide eliminar múltiples pagos o artículos legítimos.
- **Huérfanos**: registros hijos que apuntan a un identificador de padre inexistente.
- **Idempotencia**: repetir la ejecución sin introducir duplicados ni resultados injustificados.

**Condición para pasar a Fase 3.** Los nueve CSV deben estar en RAW, los conteos y estructuras deben coincidir con la fuente, y los informes de claves, referencias y vacíos deben poder revisarse. Las anomalías detectadas se documentan, no se corrigen todavía.
