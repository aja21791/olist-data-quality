# Fase 4. Limpieza y transformación de RAW a CLEAN

**Contexto:** ya terminaste las fases 1–3. El reporte inicial `reports/quality_reports/phase3_quality_report.md` define la línea base; no se pueden asignar aquí cifras reales sin ejecutar tu base local.

## Paso 1: Entender qué vamos a construir

**1. ¿Qué realizaremos?** Ejecutaremos 15 reglas SQL sobre los nueve conjuntos Olist. Python orquestará el flujo y generará las nueve tablas de `clean` sin cambiar RAW.

**2. ¿Por qué?** Separar el origen inmutable de las correcciones permite auditar decisiones y reconstruir resultados. La limpieza debe preservar observaciones legítimas, múltiples artículos por pedido y pagos mixtos.

**3. ¿Dónde trabajamos?** Terminal PowerShell de VS Code, carpeta raíz `olist-data-quality`, entorno Conda `olist-quality`.

**4. Implementación:** desde la terminal integrada:

```powershell
conda activate olist-quality
python -m src.diagnose --no-figures
python -m src.transform
python -m pytest -q tests
```

No vuelvas a descargar CSV ni ejecutes `python -m src.extract --force-reload` si RAW ya está cargado correctamente. Para proteger tu trabajo, haz una copia de `database/ecommerce.duckdb` con VS Code y Jupyter cerrados antes de reemplazar archivos del paquete.

**5. Explicación del código:** `src.transform` valida columnas RAW; crea un catálogo de reglas en `audit`; dentro de una transacción usa SQL tipado y tablas **TEMPORARY**, evalúa claves y cuarentena, construye CLEAN y ejecuta indicadores. Si una validación crítica falla, ejecuta ROLLBACK. El esquema RAW se consulta y nunca se actualiza.

**6. Resultados esperados:** nueve tablas CLEAN, 15 identificadores de reglas en `audit.quality_rule_catalog`, resultados en `audit.rule_results`, registros problemáticos en `audit.quarantine_records`, y los archivos `phase4_*` en `reports/quality_reports`.

**7. Comprobación:** ejecuta las siguientes consultas en una nueva celda del notebook (o mediante Python con conexión `duckdb.connect`) después de terminar:

```sql
SELECT table_schema, table_name FROM information_schema.tables
WHERE table_schema IN ('raw','clean') ORDER BY 1,2;

SELECT rule_id, status, SUM(detected) AS detectadas, SUM(unresolved) AS pendientes
FROM audit.rule_results
WHERE run_id = (SELECT run_id FROM audit.transform_runs ORDER BY started_at DESC LIMIT 1)
GROUP BY rule_id, status ORDER BY rule_id;

SELECT source_table, reason, COUNT(*) AS registros
FROM audit.quarantine_records
GROUP BY 1,2 ORDER BY 1,2;
```

**8. Problemas frecuentes:** `ModuleNotFoundError: duckdb` → activa Conda y comprueba el kernel; `Missing column` → verifica los nueve encabezados RAW con `python -m src.understand`; bloqueo del archivo → cierra conexiones a DuckDB; error de conversión inesperado → inspecciona el campo en el CSV original y no cambies los valores arbitrariamente. Si aparece un error SQL en tu instalación, guarda el texto completo y revisa qué instrucción requiere adaptarse: esta entrega no se ha ejecutado contra tu base real.

**9. Resultado:** cuando concluye sin fallos críticos, CLEAN existe y cada diferencia de filas está justificada por la cuarentena. **Aún NO se han exportado Parquet**: primero deben aprobarse las pruebas formales de Fase 5.

## Arquitectura inalterada

```text
CSV originales → Python + DuckDB → RAW → 15 reglas SQL → CLEAN → pruebas → Parquet + reporte final
                                      ↘ auditoría / cuarentena ↗
```

`audit` no es un staging adicional. Los objetos `_typed_*`, `_candidate_*`, `_groups_*` y `_classified_*` son tablas temporales de la conexión, utilizadas únicamente durante la transformación.

## Mapa de herramientas y archivos

| Ruta | Función |
|---|---|
| `src/transform.py` | Coordinador Python, control de calidad, auditoría y transacción |
| `src/transform_schema.py` | Modelo de columnas, tipos y claves esperadas (verificadas contra RAW) |
| `sql/transformations/tables/*.sql` | 9 SELECT completos con TRY_CAST y normalizaciones |
| `sql/transformations/rules/01_*.sql` a `15_*.sql` | Diagnósticos y verificaciones SQL de las 15 reglas |
| `audit.transform_runs` | Identificadores, fecha, estado, error de ejecuciones |
| `audit.quality_rule_catalog` | Reglas, severidades y políticas |
| `audit.rule_results` | Detectadas, corregidas, cuarentena, pendientes y PASS/WARNING/FAIL |
| `audit.quarantine_records` | Registro de originales rechazados, motivo y hash |
| `reports/quality_reports/phase4_*` | Resumen y conciliación de conteos RAW/CLEAN/cuarentena |

### Conocer la diferencia entre corrección, advertencia y cuarentena

- **Corrección:** formato ajustado con una regla establecida (ej.: espacios en ciudad). No se modifica RAW.
- **Advertencia:** valor atípico, referencia huérfana o discrepancia monetaria que no justifica modificar el original.
- **Cuarentena:** fila excluida de CLEAN cuando no puede representarse confiablemente (claves obligatorias, valores tipados inválidos o contradicción en clave de negocio). El JSON original se guarda en auditoría.
- **FAIL:** una validación crítica falló y se revierte toda la transformación.

La coincidencia del texto normalizado NO prueba que la fuente original fuera incorrecta. Todo indicador debe interpretarse conforme al negocio.

## Paso 2. Las 15 actividades obligatorias: A–J

Cada actividad tiene un archivo SQL que **sí se ejecuta**, y participa en la construcción o comprobación de CLEAN. Ejecuta las reglas dentro de `python -m src.transform`; no ejecutes aisladamente las consultas que usan tablas temporales, porque las tablas TEMP dejan de existir cuando se cierra la conexión.


### Actividad 01. Duplicados

**A. Concepto.** Una misma clave identifica una entidad más de una vez. Repetir order_id en artículos o pagos NO es duplicación.

**B. Importancia.** Podría duplicar ventas, clientes o pagos cuando se agregan cifras.

**C. Diagnóstico.** Agrupar por la clave natural propuesta y calcular sum(n−1). Consulta versionada: `sql/transformations/rules/01_duplicados.sql`.

**D. Resultados.** El conteo se obtiene desde el SQL y se visualiza en `phase4_rule_results.csv` (columna `detected`, filtrando `R01`). No se inventan cantidades.

**E. Interpretación.** Comparar grupos conflictivos, repetidos exactos y filas legítimas de tablas sin clave única.

**F. Política de limpieza.** Sólo se deduplican tablas con clave candidata: orders, order_items, order_payments, customers, products, sellers y category_translation. Los grupos con diferentes versiones normalizadas se llevan completos a cuarentena; reseñas y geolocalización no se deduplican.

**G. Implementación.** _groups_* calcula variantes; _classified_* usa ROW_NUMBER() por clave y hash para reproducibilidad. SQL: `sql/transformations/rules/01_duplicados.sql`; orquestación: `src/transform.py`.

**H. Validación.** En una conexión a DuckDB: `SELECT order_id, COUNT(*) FROM clean.order_items GROUP BY order_id; -- puede devolver >1 legítimamente`.

**I. Documentación.** audit.quarantine_records guarda REDUNDANT_KEY_ROW o BUSINESS_KEY_CONFLICT.

**J. Conclusión.** Se preserva granularidad, pero conflictos exigen revisión de la fuente.


### Actividad 02. Valores nulos

**A. Concepto.** Una celda ausente puede ser legítima o impedir identificar un pedido.

**B. Importancia.** Eliminar todos los NULL sesga la muestra y puede romper relaciones.

**C. Diagnóstico.** Contar nulos y cadenas vacías en REQUIRED por tabla, distinguiendo columnas opcionales. Consulta versionada: `sql/transformations/rules/02_nulos.sql`.

**D. Resultados.** El conteo se obtiene desde el SQL y se visualiza en `phase4_rule_results.csv` (columna `detected`, filtrando `R02`). No se inventan cantidades.

**E. Interpretación.** Interpretar faltantes con el estado del pedido; un evento de despacho vacío no siempre es error.

**F. Política de limpieza.** Campos críticos ausentes a cuarentena; campos opcionales se conservan como NULL y se documentan.

**G. Implementación.** _missing_required agrega pruebas por columna; SQL transforma sólo vacíos textuales a NULL. SQL: `sql/transformations/rules/02_nulos.sql`; orquestación: `src/transform.py`.

**H. Validación.** En una conexión a DuckDB: `SELECT COUNT(*) FROM clean.orders WHERE order_id IS NULL; -- debe ser cero`.

**I. Documentación.** R02 y motivo MISSING_REQUIRED; comparar filas RAW,CLEAN y cuarentena.

**J. Conclusión.** La completitud debe definirse por contexto, no como ausencia absoluta de NULL.


### Actividad 03. Estandarización de texto

**A. Concepto.** Ciudades, estados y categorías pueden diferir en espacios y capitalización.

**B. Importancia.** Fragmentan grupos y agregaciones, aun cuando representan el mismo valor.

**C. Diagnóstico.** Contar valores que difieren entre el texto RAW y su representación normalizada. Consulta versionada: `sql/transformations/rules/03_texto.sql`.

**D. Resultados.** El conteo se obtiene desde el SQL y se visualiza en `phase4_rule_results.csv` (columna `detected`, filtrando `R03`). No se inventan cantidades.

**E. Interpretación.** Comparar categorías antes y después y localizar cambios que produzcan un colapso semántico.

**F. Política de limpieza.** TRIM, REGEXP_REPLACE, LOWER o UPPER en descripciones; no quitar acentos ni alterar IDs.

**G. Implementación.** Las consultas de tablas usan TRIM/LOWER/UPPER/REGEXP_REPLACE; _normalization cuenta filas afectadas. SQL: `sql/transformations/rules/03_texto.sql`; orquestación: `src/transform.py`.

**H. Validación.** En una conexión a DuckDB: `SELECT DISTINCT customer_state FROM clean.customers ORDER BY 1;`.

**I. Documentación.** R03 almacena afectados y corregidos, con RAW inalterado.

**J. Conclusión.** Se unifican variaciones de formato sin inventar equivalencias semánticas.


### Actividad 04. Conversión de tipos

**A. Concepto.** El CSV carece de tipos SQL; los valores importados permanecen VARCHAR en RAW.

**B. Importancia.** Una suma financiera sobre texto o una fecha inválida puede alterar resultados o fallar.

**C. Diagnóstico.** TRY_CAST para DECIMAL, INTEGER, DOUBLE y TIMESTAMP, con el valor original disponible en _src_. Consulta versionada: `sql/transformations/rules/04_tipos.sql`.

**D. Resultados.** El conteo se obtiene desde el SQL y se visualiza en `phase4_rule_results.csv` (columna `detected`, filtrando `R04`). No se inventan cantidades.

**E. Interpretación.** Si la fuente no vacía no convierte, el error es medible; no se sustituye por cero.

**F. Política de limpieza.** Conversiones válidas se guardan tipadas; conversiones fallidas no vacías quedan en cuarentena.

**G. Implementación.** Los nueve archivos tables/*.sql generan _src_* y TRY_CAST; _invalid_cast registra errores. SQL: `sql/transformations/rules/04_tipos.sql`; orquestación: `src/transform.py`.

**H. Validación.** En una conexión a DuckDB: `DESCRIBE clean.order_payments; -- payment_value debe tener tipo DECIMAL`.

**I. Documentación.** R04 y INVALID_CAST con JSON original completo.

**J. Conclusión.** CLEAN tipifica valores confiables y no silencia pérdidas de formato.


### Actividad 05. Fechas inconsistentes

**A. Concepto.** Aprobación, entrega al transportista y recepción deben tener una secuencia lógica.

**B. Importancia.** Afectan indicadores de tiempo y confianza en el proceso de entrega.

**C. Diagnóstico.** Comparar eventos sólo cuando ambas fechas existan, con COUNT y predicados. Consulta versionada: `sql/transformations/rules/05_fechas.sql`.

**D. Resultados.** El conteo se obtiene desde el SQL y se visualiza en `phase4_rule_results.csv` (columna `detected`, filtrando `R05`). No se inventan cantidades.

**E. Interpretación.** Distinguir cronologías imposibles de entregas más tardías que las estimadas.

**F. Política de limpieza.** Se mantienen y reportan sospechas; sin fuente externa no se inventan fechas.

**G. Implementación.** CLEAN tipifica timestamps y la regla SQL marca secuencias sospechosas. SQL: `sql/transformations/rules/05_fechas.sql`; orquestación: `src/transform.py`.

**H. Validación.** En una conexión a DuckDB: `SELECT COUNT(*) FROM clean.orders WHERE order_delivered_customer_date < order_purchase_timestamp;`.

**I. Documentación.** R05 registra alertas pendientes para revisión del dominio.

**J. Conclusión.** La interpretación temporal requiere contexto y calidad de origen.


### Actividad 06. Precios inválidos

**A. Concepto.** Precios, fletes y pagos deben tener valores numéricos coherentes.

**B. Importancia.** Montos negativos anómalos pueden falsear ingresos y métricas.

**C. Diagnóstico.** Contar importes negativos; detectar fallos de CAST por separado. Consulta versionada: `sql/transformations/rules/06_precios.sql`.

**D. Resultados.** El conteo se obtiene desde el SQL y se visualiza en `phase4_rule_results.csv` (columna `detected`, filtrando `R06`). No se inventan cantidades.

**E. Interpretación.** No tratar cada cero automáticamente como error: puede existir promoción o ajuste.

**F. Política de limpieza.** Negativos y no convertibles se cuarentenan; ceros se conservan y revisan.

**G. Implementación.** _invalid_domain excluye valores negativos; R06 confirma ausencia de negativos en CLEAN. SQL: `sql/transformations/rules/06_precios.sql`; orquestación: `src/transform.py`.

**H. Validación.** En una conexión a DuckDB: `SELECT COUNT(*) FROM clean.order_items WHERE price < 0 OR freight_value < 0;`.

**I. Documentación.** Motivo INVALID_DOMAIN/INVALID_CAST y métricas R06.

**J. Conclusión.** Los importes de CLEAN se interpretan sin reemplazos ficticios.


### Actividad 07. Valores atípicos

**A. Concepto.** IQR localiza observaciones fuera de Q1−1.5·IQR y Q3+1.5·IQR.

**B. Importancia.** Un extremo puede distorsionar estadísticas sin ser necesariamente incorrecto.

**C. Diagnóstico.** Calcular Q1,Q3,IQR y valores fuera de límites por precio, flete y pago. Consulta versionada: `sql/transformations/rules/07_atipicos.sql`.

**D. Resultados.** El conteo se obtiene desde el SQL y se visualiza en `phase4_rule_results.csv` (columna `detected`, filtrando `R07`). No se inventan cantidades.

**E. Interpretación.** Analizar con histogramas/diagramas de caja y evaluar heterogeneidad por producto.

**F. Política de limpieza.** No truncar, winsorizar ni eliminar outliers automáticamente.

**G. Implementación.** Se usan QUANTILE_CONT y CTE con límites; R07 reporta pendientes. SQL: `sql/transformations/rules/07_atipicos.sql`; orquestación: `src/transform.py`.

**H. Validación.** En una conexión a DuckDB: `SELECT QUANTILE_CONT(price,0.25), QUANTILE_CONT(price,0.75) FROM clean.order_items;`.

**I. Documentación.** R07 indica cantidad de extremos y cuartiles.

**J. Conclusión.** Los extremos son evidencia para análisis adicional, no corrección automática.


### Actividad 08. Normalización de categorías

**A. Concepto.** Los códigos de categoría pueden necesitar un catálogo de correspondencias.

**B. Importancia.** Sin mapeo consistente, las ventas por categoría son poco confiables.

**C. Diagnóstico.** Cruzar products contra category_translation tras normalizar texto. Consulta versionada: `sql/transformations/rules/08_categorias.sql`.

**D. Resultados.** El conteo se obtiene desde el SQL y se visualiza en `phase4_rule_results.csv` (columna `detected`, filtrando `R08`). No se inventan cantidades.

**E. Interpretación.** La falta de traducción no significa que el producto deba descartarse.

**F. Política de limpieza.** Conservar categoría original normalizada; no inventar traducciones ni fusionar categorías por similitud.

**G. Implementación.** Products y translation normalizan con LOWER/TRIM; R08 usa NOT EXISTS. SQL: `sql/transformations/rules/08_categorias.sql`; orquestación: `src/transform.py`.

**H. Validación.** En una conexión a DuckDB: `SELECT COUNT(*) FROM clean.products p WHERE NOT EXISTS (SELECT 1 FROM clean.category_translation c WHERE c.product_category_name=p.product_category_name);`.

**I. Documentación.** R08 registra categorías sin traducción.

**J. Conclusión.** El catálogo explícito permite cobertura evaluable sin equivalencias arbitrarias.


### Actividad 09. Integridad referencial

**A. Concepto.** La referencia de cada hijo debe existir cuando la relación es exigible.

**B. Importancia.** Una referencia huérfana puede crear pedidos incompletos o agregados erróneos.

**C. Diagnóstico.** Usar NOT EXISTS para validar hijos contra padres sin introducir multiplicaciones. Consulta versionada: `sql/transformations/rules/09_integridad.sql`.

**D. Resultados.** El conteo se obtiene desde el SQL y se visualiza en `phase4_rule_results.csv` (columna `detected`, filtrando `R09`). No se inventan cantidades.

**E. Interpretación.** Considerar el posible efecto de cuarentenas sobre la cantidad de huérfanos.

**F. Política de limpieza.** Mantener hijos huérfanos identificados para revisión y no inventar padres.

**G. Implementación.** R09 ejecuta las relaciones verificadas de model_metadata.RELATIONSHIPS excepto catálogo opcional. SQL: `sql/transformations/rules/09_integridad.sql`; orquestación: `src/transform.py`.

**H. Validación.** En una conexión a DuckDB: `SELECT COUNT(*) FROM clean.order_items i WHERE NOT EXISTS (SELECT 1 FROM clean.orders o WHERE o.order_id=i.order_id);`.

**I. Documentación.** R09 registra relación, filas afectadas y warning.

**J. Conclusión.** No se ocultan problemas relacionales mediante eliminaciones en cascada.


### Actividad 10. Estados del pedido

**A. Concepto.** Un estado como delivered debe concordar con los eventos conocidos.

**B. Importancia.** Métricas de cumplimiento y entregas pueden quedar contradictorias.

**C. Diagnóstico.** Comparar estado normalizado contra fechas de recepción y estados reconocidos. Consulta versionada: `sql/transformations/rules/10_estados.sql`.

**D. Resultados.** El conteo se obtiene desde el SQL y se visualiza en `phase4_rule_results.csv` (columna `detected`, filtrando `R10`). No se inventan cantidades.

**E. Interpretación.** No exigir fechas propias de estados posteriores en pedidos aún creados o cancelados.

**F. Política de limpieza.** Conservar estados desconocidos para revisión; sin fecha de cancelación no inventar una.

**G. Implementación.** R10 usa CASE implícito por OR y conjunto conocido de estados reales esperados. SQL: `sql/transformations/rules/10_estados.sql`; orquestación: `src/transform.py`.

**H. Validación.** En una conexión a DuckDB: `SELECT order_status, COUNT(*) FROM clean.orders GROUP BY 1 ORDER BY 2 DESC;`.

**I. Documentación.** R10 reporta alertas de estado y eventos.

**J. Conclusión.** Las validaciones dependen de reglas contextualizadas, no universales.


### Actividad 11. Códigos postales

**A. Concepto.** Olist contiene prefijos brasileños de cinco dígitos, no CEP completo.

**B. Importancia.** Perder ceros iniciales rompe segmentaciones geográficas.

**C. Diagnóstico.** Usar REGEXP_FULL_MATCH, LPAD y LENGTH después de conservar RAW como texto. Consulta versionada: `sql/transformations/rules/11_postales.sql`.

**D. Resultados.** El conteo se obtiene desde el SQL y se visualiza en `phase4_rule_results.csv` (columna `detected`, filtrando `R11`). No se inventan cantidades.

**E. Interpretación.** Una cadena inválida no demuestra inexistencia geográfica por sí sola.

**F. Política de limpieza.** Para valores numéricos de longitud 1 a 5 aplicar LPAD a 5; otros se conservan y señalan.

**G. Implementación.** La normalización de prefijos está en tables/*.sql; R11 verifica 5 dígitos. SQL: `sql/transformations/rules/11_postales.sql`; orquestación: `src/transform.py`.

**H. Validación.** En una conexión a DuckDB: `SELECT customer_zip_code_prefix FROM clean.customers LIMIT 10;`.

**I. Documentación.** R11 cuantifica prefijos con formato no validado.

**J. Conclusión.** Se conservan ceros y se evita confundir prefijo con código completo.


### Actividad 12. Conciliación de pagos

**A. Concepto.** Los montos de artículos y flete deben compararse con la suma de todos los pagos del pedido.

**B. Importancia.** Un JOIN directo N:M puede multiplicar importes y producir diferencias ficticias.

**C. Diagnóstico.** Sumar por order_id ambas tablas antes de relacionarlas con orders. Consulta versionada: `sql/transformations/rules/12_pagos.sql`.

**D. Resultados.** El conteo se obtiene desde el SQL y se visualiza en `phase4_rule_results.csv` (columna `detected`, filtrando `R12`). No se inventan cantidades.

**E. Interpretación.** Clasificar coincidentes, discrepantes y pendientes; considerar descuentos, vouchers y redondeos.

**F. Política de limpieza.** No ajustar pagos automáticamente; tolerancia nominal 0.01 y revisión de excepciones.

**G. Implementación.** CTE items, payments y check_orders. SQL: `sql/transformations/rules/12_pagos.sql`; orquestación: `src/transform.py`.

**H. Validación.** En una conexión a DuckDB: `SELECT order_id, SUM(payment_value) FROM clean.order_payments GROUP BY order_id;`.

**I. Documentación.** R12 informa conciliados, discrepantes y pendientes.

**J. Conclusión.** La conciliación confiable depende de agregar primero por la granularidad correcta.


### Actividad 13. Métodos de pago

**A. Concepto.** Las categorías textuales pueden variar por mayúsculas, espacios o valores desconocidos.

**B. Importancia.** Perjudican análisis por canal de pago y su comparabilidad.

**C. Diagnóstico.** Enumerar DISTINCT y COUNT, comparar con métodos publicados en el dataset. Consulta versionada: `sql/transformations/rules/13_metodos.sql`.

**D. Resultados.** El conteo se obtiene desde el SQL y se visualiza en `phase4_rule_results.csv` (columna `detected`, filtrando `R13`). No se inventan cantidades.

**E. Interpretación.** Una misma compra con distintos métodos no constituye un duplicado.

**F. Política de limpieza.** Normalizar mayúsculas y espacios, mantener not_defined y desconocidos para revisión.

**G. Implementación.** R13 evalúa el dominio de payment_type normalizado. SQL: `sql/transformations/rules/13_metodos.sql`; orquestación: `src/transform.py`.

**H. Validación.** En una conexión a DuckDB: `SELECT payment_type, COUNT(*) FROM clean.order_payments GROUP BY 1 ORDER BY 2 DESC;`.

**I. Documentación.** R13 conserva recuentos de valores desconocidos.

**J. Conclusión.** La normalización no elimina evidencia de métodos no catalogados.


### Actividad 14. Cadenas vacías

**A. Concepto.** Los CSV pueden representar ausencia con cadenas vacías, espacios o tokens textuales.

**B. Importancia.** Los filtros SQL contra NULL no detectan ausencias codificadas como espacios.

**C. Diagnóstico.** Contar cadenas vacías y sólo espacios separadas de NULL en RAW; verificar CLEAN. Consulta versionada: `sql/transformations/rules/14_vacias.sql`.

**D. Resultados.** El conteo se obtiene desde el SQL y se visualiza en `phase4_rule_results.csv` (columna `detected`, filtrando `R14`). No se inventan cantidades.

**E. Interpretación.** Un vacío de comentario es opcional; un ID vacío es crítico.

**F. Política de limpieza.** NULLIF(TRIM(...),'') en texto; sólo en comentarios usar marcadores null, none, nan.

**G. Implementación.** Las tablas SQL preservan la fuente y estandarizan campos de texto seleccionados. SQL: `sql/transformations/rules/14_vacias.sql`; orquestación: `src/transform.py`.

**H. Validación.** En una conexión a DuckDB: `SELECT COUNT(*) FROM clean.order_reviews WHERE review_comment_message IS NULL;`.

**I. Documentación.** R14 comprueba que los textos CLEAN no retengan cadenas sólo espacios.

**J. Conclusión.** Se distingue ausencia textual de valores desconocidos reales.


### Actividad 15. Validación final preliminar

**A. Concepto.** La construcción de CLEAN debe dejar claves críticas únicas y tipos válidos.

**B. Importancia.** Publicar datos defectuosos propagaría errores a modelos y reportes.

**C. Diagnóstico.** Contar duplicados y ausentes críticos, comprobar dominios y balance RAW=CLEAN+cuarentena. Consulta versionada: `sql/transformations/rules/15_final.sql`.

**D. Resultados.** El conteo se obtiene desde el SQL y se visualiza en `phase4_rule_results.csv` (columna `detected`, filtrando `R15`). No se inventan cantidades.

**E. Interpretación.** PASS en Fase 4 no garantiza exactitud, ni cierra excepciones no críticas.

**F. Política de limpieza.** Si una prueba crítica falla, ROLLBACK; no exportar Parquet en esta fase.

**G. Implementación.** La transacción envuelve todas las tablas; R15 y pruebas de conteo interrumpen el commit si fallan. SQL: `sql/transformations/rules/15_final.sql`; orquestación: `src/transform.py`.

**H. Validación.** En una conexión a DuckDB: `SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'clean';`.

**I. Documentación.** R15 registra estado por tabla en audit.rule_results.

**J. Conclusión.** La prueba integral formal de publicación corresponde a la Fase 5.


## Paso 3. Inspeccionar resultados y razonar cada diferencia

**1. ¿Qué haremos?** Compararemos los nueve conteos y entenderemos las excepciones. No basta con que un script finalice: si hay datos en cuarentena, debes justificar su causa.

**2. ¿Por qué?** Un total de filas de CLEAN menor que RAW no es automáticamente una mejora: solo es aceptable si cada fila ausente está registrada y la política es legítima.

**3. ¿Dónde?** Notebook `notebooks/01_data_quality_etl.ipynb`, sección Fase 4, y `reports/quality_reports/`.

**4. Implementación:**

```powershell
python -m src.transform
python -m pytest -q tests
```

**5. Explicación:** `phase4_clean_overview.csv` debe cumplir `raw_rows = clean_rows + quarantined_rows` en cada tabla; las reglas R05/R07/R09/R10/R12/R13 pueden reportar WARNING porque no deben arreglarse automáticamente.

**6. Resultados:** gráficos de filas antes/después desde el notebook, tablas de reglas y un reporte markdown con decisiones trazables.

**7. Comprobación:** ejecuta de nuevo `python -m src.transform` y comprueba que los conteos de CLEAN no cambian. El catálogo de auditoría sí conserva un historial por ejecución, por lo que tendrá nuevas filas con distintos `run_id`.

**8. Problemas frecuentes:** si el conteo aumenta sin justificación, revisa cardinalidades y no hagas `JOIN` muchos-a-muchos; si `phase4_rule_results.csv` no aparece, comprueba que terminó el commit y que la ruta de reportes es escribible; si hay discrepancias de pagos, revisa vouchers y no fuerces diferencias a cero.

**9. Resultado:** datos tipados y normalizados en CLEAN, excepciones registradas y evidencia para diseñar la Fase 5 de pruebas automatizadas formales.

## Paso 4. Consideraciones sobre validaciones, publicación y limitaciones

- En esta fase R15 es un **control preliminar**; la Fase 5 agregará pruebas automatizadas y políticas de publicación. No publicar Parquet hasta contar con un gate crítico formal.
- Los indicadores de exactitud no se calculan sin evidencia externa; la base Olist no aporta por sí sola una referencia independiente.
- Un OUTLIER no equivale a un error: puede ser una compra real cara.
- La geolocalización y las reseñas carecen de una clave única segura para deduplicar; se conservan las filas repetidas en CLEAN.
- Un valor postal de cuatro dígitos puede representar un prefijo con cero inicial perdido en la fuente; la política de LPAD sólo aplica a cadenas numéricas válidas de hasta cinco caracteres, y debe revisarse con el dominio si fuera necesario.
- Las discrepancias entre totales pagados y artículos no prueban fraude. Se consideran descuentos, vales, anulaciones y registros de estados pendientes.
- El reporte Fase 3 se conserva como evidencia inicial; el de Fase 4 documenta transformaciones y alertas pendientes. La Fase 5 formalizará una comparación de tasas por dimensión.

### Posibles mensajes de error útiles

```text
Faltan tablas RAW
```
Ejecuta `python -m src.extract` sólo si todavía no hay carga en RAW. La fase 2 debe completarse antes.

```text
La estructura RAW de <tabla> cambió
```
No fuerces un campo inexistente. Inspecciona los encabezados `DESCRIBE raw.<tabla>`, consulta el CSV y documenta el cambio.

```text
La validación inicial CLEAN falló
```
Se ejecutó ROLLBACK y el CLEAN anterior no debería haber cambiado. Inspecciona la regla y corrige la implementación sólo tras comprobar los datos.

### Próxima fase

**Fase 5. Validación formal:** pruebas de esquema, claves, relaciones, valores monetarios, coherencia temporal, estados, conciliación y repetibilidad; clasificación consistente de FAIL/WARNING; bloqueo de publicación ante fallos críticos. La exportación verificada de Parquet queda para la Fase 7, respetando el orden completo del pipeline.
