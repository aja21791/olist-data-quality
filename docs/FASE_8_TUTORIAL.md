# Fase 8 — Evaluación final, calidad comparativa y cierre documentado

**Proyecto:** Olist Data Quality (Python 3.12 + DuckDB 2.0 + SQL + Parquet).  
**Última dependencia funcional:** Fase 7 R05, con release `PUBLISHED` y `VERIFIED`.  
**Arquitectura inalterable:** CSV originales → Python + DuckDB → RAW → 15 reglas SQL → CLEAN → validación → Parquet + reporte. `audit` es auxiliar; no existe STAGING persistente.

> **Alcance:** esta fase no reescribe los CSV, RAW, CLEAN, AUDIT ni archivos Parquet. Lee la base y los Parquet publicados y produce reportes de evaluación. **No** crea una nueva publicación, ni afirma exactitud externa. Los ejemplos de cantidades dependen de lo que mida el programa en tu equipo.

## Paso 1. Preparar entorno y conservar evidencia

**1. ¿Qué vamos a realizar?** Trabajar sobre la versión más reciente del proyecto (cierre R05), sin perder el release generado.  
**2. ¿Por qué es necesario?** El informe final debe poder vincular resultados de transformaciones, validaciones y los nueve Parquet con los mismos identificadores.  
**3. ¿Dónde debemos trabajar?** Terminal integrada de VS Code, directorio raíz `olist-data-quality`.  
**4. Implementación:**

```powershell
conda activate olist-quality
python -m pytest -q tests
python -m src.verify_release
Get-Content .\data\clean\release_manifest.json | Select-Object -First 15
```

Haz una **copia de seguridad externa** del proyecto, especialmente `database/ecommerce.duckdb`, `data/clean/` y `reports/quality_reports/`. Si instalas un parche, reemplaza sólo código/documentación: no reemplaces tu base ni los Parquet.  
**5. Explicación:** `verify_release` lee realmente cada Parquet, coteja tamaño, columnas, tipos, filas, SHA-256 y el CSV auxiliar de R05.  
**6. Resultados esperados:** todas las pruebas sin fallos y estado `VERIFIED`. No imponemos un número de tests: depende de los módulos instalados.  
**7. Comprobación:** debe existir `data/clean/release_manifest.json` y `reports/quality_reports/phase7_verification_summary.json`, con el mismo `release_id`.  
**8. Problemas frecuentes:** si no existe el manifiesto, terminar Fase 7; si DuckDB está bloqueado, cierra otras conexiones; si falla SHA-256, no sobrescribas el archivo para «hacerlo pasar»: investiga y, si procede, vuelve a publicar.  
**9. Resultado:** evidencia de publicación preservada.

## Paso 2. Conocer las dimensiones y métricas

**1. ¿Qué vamos a realizar?** Contrastar seis dimensiones entre RAW y CLEAN, respetando la granularidad.  
**2. ¿Por qué es necesario?** Un porcentaje sin denominador no permite evaluar la calidad, y la suma de incidentes en distintas tablas no son «pedidos defectuosos».  
**3. ¿Dónde debemos trabajar?** `src/evaluate.py` y `sql/evaluation/*.sql`.  
**4. Implementación:** los controles SQL entregados calculan recuentos a partir de tablas reales. Las métricas son:

| Dimensión | Numerador | Denominador | Observaciones |
|---|---|---|---|
| Completitud | NULL o cadena vacía en **campos obligatorios** | Suma de oportunidades de campos obligatorios | No penalizar campos opcionales legítimamente ausentes |
| Unicidad | Filas en exceso por claves de negocio verificadas | Suma de filas de tablas con esas claves | No imponer unicidad a `geolocation`/`order_reviews` |
| Validez monetaria | RAW no convertible/negativo; CLEAN negativo | Suma de campos monetarios evaluados | RAW inválido puede estar en cuarentena; conservar auditoría |
| Dominios numéricos | Valores fuera de rango o no convertibles en RAW; fuera de rango en CLEAN | Suma de campos de dominio evaluados | Incluir coordenadas, puntuaciones y dimensiones |
| Consistencia temporal | Comparaciones de fechas contradictorias | Pedidos × número de controles temporales | Se cuentan **eventos**, no necesariamente pedidos únicos |
| Integridad referencial | FK ausente o sin padre en relaciones obligatorias | Suma de oportunidades FK | Traducciones opcionales se excluyen del índice obligatorio |

**5. Explicación:** la tasa por control es `100 × incidencias / oportunidades`. La tasa agregada de una dimensión acumula numerador y denominador **solo de controles con la misma unidad**. Un `0/0` no se transforma en un aparente `0%`; se representa como `N/D`.  
**6. Resultados esperados:** `phase8_quality_metrics.csv` (por control) y `phase8_dimension_summary.csv` (por dimensión).  
**7. Comprobación:** filtra en CSV por `metric_id` y confirma numerador, denominador, definición y notas; revisa que no se suman indiscriminadamente registros de distintas tablas.  
**8. Problemas frecuentes:** una disminución a cero por cuarentena o supresión documentada **no equivale a corregir** el dato externo; revisar la columna `note`.  
**9. Resultado:** criterios comparables y defendibles.

### Ejemplo de las consultas SQL implementadas

Estas consultas son ilustrativas de los SQL versionados; el evaluador carga los archivos de `sql/evaluation/` y cita identificadores del catálogo interno.

```sql
-- Conteo de nulos originales en un campo obligatorio (RAW)
SELECT COUNT(*) FILTER (
  WHERE NULLIF(TRIM(customer_id), '') IS NULL
) AS faltantes
FROM raw.customers;

-- Calidad temporal después del tratamiento R05 (CLEAN)
SELECT carrier_timestamp_quality_status, COUNT(*) AS pedidos
FROM clean.orders_temporal_quality
GROUP BY 1
ORDER BY 1;
```

La primera consulta conserva el significado de campo obligatorio; la segunda **distingue** `QUALITY_SUPPRESSED` de `SOURCE_MISSING` y `USABLE`.

## Paso 3. Ejecutar la evaluación automatizada

**1. ¿Qué vamos a realizar?** Ejecutar toda la comparación, auditoría, diccionario, reportes y gráfica.  
**2. ¿Por qué es necesario?** El informe final debe surgir del estado vigente de DuckDB, no de cifras copiadas del notebook.  
**3. ¿Dónde debemos trabajar?** Terminal de VS Code, raíz `olist-data-quality`.  
**4. Implementación:**

```powershell
python -m src.evaluate
```

Para evaluación exhaustiva del contenido de todas las filas de cada Parquet, incluyendo duplicados:

```powershell
python -m src.evaluate --deep-compare
```

Si la gráfica genera problemas en un servidor sin interfaz:

```powershell
python -m src.evaluate --no-figures --deep-compare
```

**5. Explicación:** el módulo comprueba: (a) integridad de Parquet, (b) que `transform_run_id` coincida con el último RAW → CLEAN terminado, (c) que la validación asociada esté aprobada, (d) que existan 15 reglas, (e) métricas antes/después, (f) eventos R05, (g) integridad del diccionario. `--deep-compare` utiliza `EXCEPT ALL` en ambos sentidos, por lo que **también detecta diferencias en multiplicidad de filas**, cosa que un `EXCEPT` normal no garantizaría.  
**6. Resultados esperados:** `EVALUATED` o `EVALUATED_WITH_WARNINGS`; nueve Parquet verificados. Las advertencias auténticas permanecen visibles.  
**7. Comprobación:** revisa el ID en `phase8_summary.json` y su correspondencia con `release_manifest.json`; ambos deben referenciar el mismo release.  
**8. Problemas frecuentes:** `Release obsoleto` significa que reconstruiste CLEAN después de publicar; repite `python -m src.release --publish` si el gate sigue conforme y luego reevalúa. `EXCEPT ALL` puede consumir más RAM; sin esa opción se mantienen las verificaciones SHA-256/conteos/esquemas.  
**9. Resultado:** evaluación vinculada a un snapshot publicado real.

## Paso 4. Interpretar el tratamiento R05

**1. ¿Qué vamos a realizar?** Distinguir el estado técnico de CLEAN del diagnóstico de la fuente.  
**2. ¿Por qué es necesario?** La fecha del transportista no se «arregló»: se declaró no verificable y se hizo inutilizable para cálculos logísticos afectados.  
**3. ¿Dónde debemos trabajar?** `phase8_final_report.md`, `phase8_temporal_events.csv` y vista `clean.orders_temporal_quality`.  
**4. Implementación:**

```sql
SELECT issue_code, COUNT(*) AS eventos, COUNT(DISTINCT order_id) AS pedidos
FROM audit.temporal_policy_events
WHERE transform_run_id = (
    SELECT run_id
    FROM audit.transform_runs
    WHERE status='COMPLETED'
    ORDER BY finished_at DESC NULLS LAST, run_id DESC
    LIMIT 1
)
GROUP BY 1 ORDER BY 1;
```

**5. Explicación:** se cuentan eventos por tipo, y **aparte** los pedidos únicos. La política conserva timestamp original en RAW/AUDIT y hace `NULL` en CLEAN el evento transportista contradictorio.  
**6. Resultados esperados:** se contabilizarán los eventos según el dataset de tu versión; en los reportes del Olist real suministrados en noviembre de 2026 se identificaron **189** eventos, con **166** transportista antes de compra y **23** entrega antes de transportista. **Esos números no están fijados en el código.**  
**7. Comprobación:** `V09.TEMPORAL_POLICY_AUDIT` debe permanecer `PASS`, y `V09.TEMPORAL_SOURCE_FLAGGED` puede seguir en `WARNING`.  
**8. Problemas frecuentes:** nunca igualar `0` incidencias en CLEAN con `0` errores de la fuente; el primer número describe **conformidad de salida**, no certeza sobre eventos reales.  
**9. Resultado:** investigación de origen pendiente debidamente preservada.

## Paso 5. Catálogo de reglas, pruebas y diccionario

**1. ¿Qué vamos a realizar?** Documentar 15 reglas, sus incidencias, los resultados de validación y los 52 campos de las nueve tablas.  
**2. ¿Por qué es necesario?** La calidad es auditable si otra persona conoce qué reglas corrieron y qué atributos puede utilizar.  
**3. ¿Dónde debemos trabajar?** `reports/quality_reports/` y `reports/data_dictionary/`.  
**4. Implementación:** al ejecutar `python -m src.evaluate` se exportan:

- `phase8_rule_catalog.csv`: R01–R15, descripción, política, severidad y recuentos de ejecución.
- `phase8_validation_checks.csv`: controles de Fase 5 asociados **al release**, estados, incidencias y universos.
- `phase8_data_dictionary.csv`: nombre, descripción, granularidad, tipos RAW/CLEAN, faltantes y reglas de calidad por campo.

**5. Explicación:** los datos provienen de `audit.rule_results`, `audit.quality_rule_catalog`, `audit.validation_results` y `DESCRIBE raw/clean`, no de un diccionario imaginado.  
**6. Resultados esperados:** 15 entradas de reglas, nueve tablas, 52 campos; los conteos de incidencia provienen de la ejecución.  
**7. Comprobación:** el reporte debe explicar si faltantes en `order_reviews.review_comment_message` son legítimos (opcionales) y que `customer_id` es distinto de `customer_unique_id`.  
**8. Problemas frecuentes:** no utilizar `geolocation_zip_code_prefix` ni `review_id` como claves únicas si la granularidad no lo justifica.  
**9. Resultado:** base documental reproducible.

## Paso 6. Analizar la gráfica RAW vs CLEAN

**1. ¿Qué vamos a realizar?** Visualizar tasas de incidencias de seis dimensiones.  
**2. ¿Por qué es necesario?** Una gráfica con métricas definidas permite comunicar resultados sin ocultar los denominadores.  
**3. ¿Dónde debemos trabajar?** `reports/figures/phase8_before_after_quality.png` y Notebook.  
**4. Implementación:** `python -m src.evaluate` genera la figura mediante Matplotlib.  
**5. Explicación:** la gráfica muestra porcentajes de incidencias por **oportunidades de control**, no un porcentaje universal de «datos erróneos».  
**6. Resultados esperados:** dos barras (RAW y CLEAN) por dimensión, siempre calculadas a partir de los CSV de métricas.  
**7. Comprobación:** contrastar visualmente cada cifra con `phase8_dimension_summary.csv`; no duplicar fotografías de gráficas como evidencia no reproducible.  
**8. Problemas frecuentes:** si varias tasas son cero, ello no garantiza exactitud; revisar los `WARNING`.  
**9. Resultado:** evidencia visual sólida del proceso.

## Paso 7. Revisar el informe final y el cierre

**1. ¿Qué vamos a realizar?** Verificar criterios de aceptación y declarar pendientes reales.  
**2. ¿Por qué es necesario?** El cierre profesional distingue **cumplimiento técnico** de **exactitud externa** y de decisiones de negocio pendientes.  
**3. ¿Dónde debemos trabajar?** `reports/quality_reports/phase8_final_report.md` y `phase8_summary.json`.  
**4. Implementación:**

```powershell
python -m src.evaluate --deep-compare
python -m pytest -q tests
Get-Content .\reports\quality_reports\phase8_final_report.md
```

**5. Explicación:** el informe enlaza IDs y evidencia de RAW/CLEAN/Parquet, documenta las seis dimensiones, todas las reglas, pruebas y excepciones, y limita sus conclusiones a hechos comprobados.  
**6. Resultados esperados:** cierre técnico **con advertencias** si siguen existiendo y `PASS_WITH_WARNINGS` en validación; publicación `PUBLISHED`/`VERIFIED`.  
**7. Comprobación:** adjuntar salida de `pytest` (no es ejecutado por el evaluador), y verificar mediante la opción `--deep-compare` que el contenido actual de CLEAN coincide con cada Parquet.  
**8. Problemas frecuentes:** si `EVALUATED_WITH_WARNINGS`, **no significa fracaso**: lee en el informe qué problemas se conservan y cuáles están técnicamente tratados. Si se reconstruye CLEAN, vuelve a pasar por la puerta de Fase 7 antes del nuevo cierre.  
**9. Resultado:** evaluación final lista para documentarse académica y profesionalmente.

## Paso 8. Ejecutar todo el proyecto desde cero (reproducibilidad)

**1. ¿Qué vamos a realizar?** Repetir el proceso en otro entorno o máquina.  
**2. ¿Por qué es necesario?** La reproducibilidad demuestra que el resultado no depende de intervenciones manuales ocultas.  
**3. ¿Dónde debemos trabajar?** Terminal VS Code, con los nueve CSV originales descargados y el entorno Conda instalado.  
**4. Implementación:**

```powershell
conda activate olist-quality
python -m src.verify_setup --require-data
python -m pytest -q tests
python -m src.pipeline --no-figures --publish --evaluate --deep-compare
python -m src.verify_release
```

**5. Explicación:** el pipeline recorre CSV → RAW → CLEAN → controles y publica **solamente** cuando la puerta de calidad lo permite; la evaluación requiere el release publicado.  
**6. Resultados esperados:** nueve Parquet y reportes solo si se satisfacen los controles. Cualquier nuevo fallo crítico debe bloquear, como ocurrió antes del tratamiento R05.  
**7. Comprobación:** `phase8_summary.json.release_id` debe coincidir con el manifiesto; los conteos y tipos deben conciliar.  
**8. Problemas frecuentes:** errores en CSV originales, cambios de distribución, permisos de Windows, falta de espacio, cierre abrupto durante sustitución, o salidas con calidad distinta: investigar y no saltarse el gate.  
**9. Resultado:** proyecto demostrable como evidencia práctica de SQL, Python, Data Engineering y Data Quality.

## Qué representa exactamente el cierre

- **Completado técnicamente:** pipeline implementado, RAW y CLEAN, R01–R15, pruebas críticas, Parquet y verificación de publicación, métricas finales/diccionario/reportes, cuando el evaluador termina correctamente en el entorno real.
- **Pendiente externamente:** investigación de las 189 anomalías de la fuente, conciliación de algunos pedidos y exactitud frente a sistemas de origen.
- **No se debe afirmar:** que todos los datos son exactos, que los 189 timestamps fueron corregidos o que `PASS_WITH_WARNINGS` significa ausencia de problemas.
- **No crear STAGING persistente** ni añadir archivos de datos de producción al ZIP del código. La base y los Parquet permanecen en tu equipo; comparte sólo lo necesario para evidenciar el proceso.
