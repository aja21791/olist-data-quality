# Fase 3 — Diagnóstico integral de calidad de datos (Olist)

**Estado:** código de diagnóstico preparado; cifras reales pendientes de ejecutar en tu computadora sobre los CSV de Kaggle. No se modifican CSV ni RAW; CLEAN aún no se construye.

## Arquitectura que debemos respetar

```text
CSV originales → extracción Python + DuckDB → RAW
  → [Diagnóstico de la Fase 3, sólo lectura]
  → 15 reglas SQL de transformación → CLEAN
  → pruebas críticas y publicación → Parquet + reporte FINAL
```

La Fase 3 produce el **reporte INICIAL** necesario para comparar el antes y después. **No es el reporte final** de ETL. La publicación Parquet se reserva para después de CLEAN y sus pruebas críticas.

### Conceptos previos

- **Perfilado (profiling):** explorar cada tabla para medir su forma, completitud, distribución, unicidad, consistencia y relaciones.
- **Granularidad:** qué representa una fila. En `order_items`, representa un artículo de pedido; en `order_payments`, un registro de pago. Repetir `order_id` ahí no significa duplicado.
- **Clave de negocio:** conjunto de atributos que identifica una entidad o evento según sus reglas. No siempre coincide con una PK física.
- **Problema de calidad:** incumplimiento de una regla justificada. Una observación sospechosa exige investigación; no autoriza por sí sola modificar datos.
- **IQR:** rango intercuartílico, calculado como Q3 − Q1. Los valores menores que Q1 − 1.5×IQR o mayores que Q3 + 1.5×IQR se consideran *potencialmente* atípicos.
- **Integridad referencial:** que los identificadores de una tabla hija apunten a un registro en la tabla padre. Las categorías de traducción son un catálogo de cobertura opcional, no una FK obligatoria.
- **Conciliación de pagos:** comparar **SUM(price + freight_value)** de artículos frente a **SUM(payment_value)**, ambas agregadas por pedido por separado para evitar el producto cartesiano entre múltiples artículos y pagos.

---

## Paso 1. Confirmar la Fase 2

**1. ¿Qué vamos a realizar?** Comprobar que el entorno y las nueve tablas RAW estén listos.

**2. ¿Por qué es necesario?** No se puede perfilar lo que no se cargó; las cifras deben salir de la base real.

**3. ¿Dónde debemos trabajar?** Terminal PowerShell de VS Code, en la raíz `olist-data-quality`, donde se encuentra `environment.yml`.

**4. Implementación:**

```powershell
conda activate olist-quality
python -m src.verify_setup --require-data
python -m src.understand
```

**5. Explicación:** `verify_setup` comprueba el entorno y los CSV; `understand` produce el diccionario y una exploración básica desde RAW sin limpiar.

**6. Resultados esperados:** archivos `raw_*.csv` en `reports/quality_reports/`, diccionario y Mermaid en `reports/data_dictionary/`.

**7. Comprobación:** verifica la existencia de `database/ecommerce.duckdb` y de los nueve resultados `raw_tables` en el reporte.

**8. Problemas frecuentes:** `ModuleNotFoundError` ⇒ activa `olist-quality`; base faltante ⇒ ejecuta `python -m src.extract` si tienes los CSV; `database is locked` ⇒ cierra otra sesión de DuckDB que tenga el archivo abierto en modo escritura.

**9. Resultado del paso:** línea de base RAW disponible.

---

## Paso 2. Ejecutar el diagnóstico integral

**1. ¿Qué vamos a realizar?** Cuantificar las anomalías sin introducir ningún cambio en los datos.

**2. ¿Por qué es necesario?** Antes de elegir la corrección de cada regla necesitamos pruebas y cifras verificables.

**3. ¿Dónde debemos trabajar?** Terminal PowerShell de VS Code. El código está en `src/diagnose.py` y ejecuta SQL de `sql/profiling/`.

**4. Implementación:**

```powershell
python -m src.diagnose
```

Sin figuras, útil si una instalación carece de soporte gráfico:

```powershell
python -m src.diagnose --no-figures
```

**5. Explicación:** `duckdb.connect(..., read_only=True)` impide escribir sobre RAW; se obtienen 19 conjuntos de diagnóstico con consultas SQL y Python sólo los coordina y exporta los resultados. Se miden duplicados exactos, claves candidatas, nulos y blancos, cardinalidad, conversiones, fechas, dinero, IQR, categorías, estados, códigos postales, integridad y conciliación.

**6. Resultados esperados:** `phase3_*.csv`, `phase3_summary.json`, `phase3_manifest.json`, `phase3_quality_report.md` y figuras `phase3_*.png`. Nunca se mostrarán cantidades supuestas: todas deben proceder de tu RAW.

**7. Comprobación:** abre `reports/quality_reports/phase3_quality_report.md` y comprueba que las nueve tablas aparezcan con sus filas, claves y referencias; revisa `phase3_payment_summary.csv`.

**8. Problemas frecuentes:** columnas faltantes ⇒ leer `reports/data_dictionary/raw_columns.csv`; fallos gráficos ⇒ `--no-figures`; consultas lentas ⇒ considera el millón de filas aprox. de geolocalización, y no cierres el proceso de diagnóstico antes de terminar.

**9. Resultado del paso:** perfil reproducible de la calidad RAW, antes de aplicar reglas.

---

## Paso 3. Interpretar correctamente los 15 temas de calidad

**1. ¿Qué vamos a realizar?** Asignar las observaciones de RAW a las futuras reglas de la transformación.

**2. ¿Por qué es necesario?** El mismo problema puede aparecer en más de una métrica. La calidad se gestiona por causa y política, no sumando alertas indiscriminadamente.

**3. ¿Dónde debemos trabajar?** `reports/quality_reports/phase3_quality_report.md` y los CSV relacionados. Abrir con VS Code o usar Pandas desde el notebook.

**4. Implementación / consultas que ya ejecuta `src.diagnose`:**

| Regla futura | Resultado de la Fase 3 | Cómo interpretarlo |
|---|---|---|
| 01 Duplicados | `phase3_duplicates.csv`, `phase3_keys.csv` | Diferenciar duplicado exacto, por clave y repetición válida |
| 02 Nulos | `phase3_missingness.csv` | Analizar obligatorios vs. opcionales y estados |
| 03 Texto | `phase3_categories.csv` | Revisar `TRIM` / minúsculas / acentos; no tocar IDs |
| 04 Tipos | `phase3_conversions.csv` | Un valor no convertible debe permanecer trazable |
| 05 Fechas | `phase3_dates.csv`, `phase3_temporal.csv` | Separar secuencias imposibles y retrasos legítimos |
| 06 Precios | `phase3_monetary_issues.csv` | Negativos, ceros y conversiones fallidas requieren políticas distintas |
| 07 Outliers | `phase3_outliers.csv` | IQR indica extremos, no errores automáticos |
| 08 Categorías | `phase3_categories.csv`, `phase3_references.csv` | Revisar catálogo de traducción |
| 09 Integridad | `phase3_references.csv` | Cuidado con relaciones uno-a-muchos |
| 10 Estados | `phase3_statuses.csv`, `phase3_temporal.csv` | No exigir fechas de etapas aún no realizadas |
| 11 Postales | `phase3_postals.csv` | Prefijos de cinco dígitos, repetición geográfica válida |
| 12 Pagos | `phase3_payment_summary.csv`, `phase3_payment_details.csv` | Comparación agregada por pedido, sin inflar importes |
| 13 Métodos | `phase3_categories.csv` | `payment_type` puede tener dominios legítimos |
| 14 Vacías | `phase3_missingness.csv` | Diferencia entre NULL, `''` y espacios |
| 15 Validación | `phase3_issues.csv`, reporte inicial | Sólo línea base; la prueba formal será después de CLEAN |

**5. Explicación:** el diagnóstico mide incidencias, pero no modifica ninguna fila. Por ejemplo, `order_approved_at` puede ser NULL si un pedido no llegó a aprobarse; un `payment_value` extremo requiere contexto antes de clasificarlo como inválido.

**6. Resultados esperados:** hipótesis y políticas preliminares para cada regla, no ajustes silenciosos.

**7. Comprobación:** cada tabla del reporte presenta conteos observados. No hay columnas inventadas: las reglas que requieran columnas ausentes se marcan como no evaluadas.

**8. Problemas frecuentes:** contar una misma fila varias veces entre reglas; confundir frecuencia de `order_id` con duplicado; afirmar que una discrepancia monetaria implica fraude; asumir que una fecha faltante es siempre inválida.

**9. Resultado del paso:** lista priorizada para el diseño de la Fase 4.

---

## Paso 4. Comprobar las estadísticas SQL

**1. ¿Qué vamos a realizar?** Inspeccionar manualmente algunas consultas centrales para aprender cómo se derivan las métricas.

**2. ¿Por qué es necesario?** Evita ver los reportes como una caja negra.

**3. ¿Dónde debemos trabajar?** `sql/profiling/*.sql` en VS Code y una celda del notebook.

**4. Implementación en el notebook:**

```python
import duckdb
from src.config import DB_PATH
with duckdb.connect(str(DB_PATH), read_only=True) as con:
    consulta = '''
    SELECT order_id, COUNT(*) AS items
    FROM raw.order_items
    GROUP BY order_id
    HAVING COUNT(*) > 1
    ORDER BY items DESC
    LIMIT 10
    '''
    display(con.execute(consulta).df())
```

**5. Explicación:** esta consulta detecta pedidos con múltiples artículos; **no busca errores**, sino que ayuda a comprobar la granularidad y por qué una deduplicación simple por `order_id` sería dañina.

**6. Resultados esperados:** filas con pedidos que contienen varios artículos (si existen), con valores de tu base.

**7. Comprobación:** inspecciona las líneas correspondientes en `raw.order_items` y confirma `order_item_id`.

**8. Problemas frecuentes:** confundir `GROUP BY order_id` con una verificación de clave. Para evaluar clave de artículos usa `(order_id, order_item_id)`.

**9. Resultado del paso:** consultas interpretadas y consistentes con el modelo de negocio.

---

## Paso 5. Ejecutar pruebas y asegurar que RAW sigue intacto

**1. ¿Qué vamos a realizar?** Ejecutar los tests de todo el proyecto e inspeccionar sus resultados.

**2. ¿Por qué es necesario?** Garantizar que los diagnósticos manejan múltiples pagos y no alteran RAW.

**3. ¿Dónde debemos trabajar?** Terminal PowerShell de VS Code, raíz del proyecto.

**4. Implementación:**

```powershell
python -m pytest -q tests
```

**5. Explicación:** las pruebas usan datos sintéticos creados en carpetas temporales, nunca sobrescriben tu base real. Se verifica, entre otros casos, la suma independiente de artículos y pagos y la repetibilidad de resultados.

**6. Resultados esperados:** pruebas `passed`; una prueba `skipped` por DuckDB ausente obliga a verificar la instalación del entorno `olist-quality`.

**7. Comprobación:** ejecuta el diagnóstico dos veces y compara los CSV de resultados; el `run_id` y la fecha del manifiesto pueden variar, pero las métricas no deberían cambiar si RAW está intacto.

**8. Problemas frecuentes:** ejecutar `pytest` desde otra carpeta, tener otro intérprete de VS Code, fallos de conexión por archivo abierto en otra aplicación.

**9. Resultado del paso:** diagnóstico inicial listo para decisiones de limpieza.

---

## Resultados que se deben revisar antes de continuar

1. Los nueve conteos de tablas son correctos y se conservan en RAW.
2. `phase3_quality_report.md` presenta observaciones **reales** y separa problemas, advertencias y eventos legítimos.
3. `phase3_payment_summary.csv` no multiplica montos por joins artículo × pago.
4. Los prefijos se validan como texto; geolocalización no tiene una clave única supuesta.
5. Los valores extremos no se eliminaron; los faltantes válidos por estado no se imputaron.
6. Todas las consultas fueron sólo de lectura.
7. Las pruebas automatizadas resultan satisfactorias en el entorno local.

**Siguiente fase:** aplicar, una por una, las 15 actividades de calidad mediante SQL DuckDB en la transformación RAW → CLEAN, auditar cada decisión y validar cada salida. Todavía no se publica Parquet.
