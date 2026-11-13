# Fase 5 — Validación formal de calidad de datos

**Proyecto:** Brazilian E-Commerce Public Dataset by Olist  
**Entorno:** Windows 11 · Miniconda (`olist-quality`) · VS Code · DuckDB 2.0.0 · Python 3.12  
**Entrada:** Tablas `raw.*`, `clean.*` y auditoría de la Fase 4.  
**Salida:** Resultados SQL de calidad, `audit.validation_runs`, `audit.validation_results`, informe Markdown, CSV y JSON.  
**Restricción:** **NO modificar RAW ni CLEAN; NO exportar Parquet todavía.**

## 0. Relación con las fases anteriores

```text
Fase 1  Entorno y organización
Fase 2  CSV originales → extracción Python + DuckDB → RAW
Fase 3  Perfilado y diagnóstico inicial de RAW
Fase 4  15 reglas SQL y Python → CLEAN + AUDIT
FASE 5  Pruebas formales: CLEAN + integridad + auditoría → PASS / FAIL / WARNING
Fase 6  Orquestación automatizada e idempotencia completa
Fase 7  Publicación condicionada: Parquet + reporte de calidad final
Fase 8  Evaluación académica y técnica
```

En esta fase no añadimos una capa STAGING. Se ejecutan consultas SQL directamente contra `clean.*` y las tablas de auditoría. Python crea el registro de cada ejecución en `audit`, sin alterar los registros de datos.

## Paso 1. Comprender qué es una prueba formal de calidad

**1. ¿Qué vamos a realizar?** Definir verificaciones ejecutables con una condición precisa de éxito. Ejemplo: la clave `(order_id, order_item_id)` debe ser única en `clean.order_items`.

**2. ¿Por qué es necesario?** Que la Fase 4 haya terminado sin errores técnicos no demuestra por sí solo que todas las reglas de negocio se cumplan. Una prueba formal vuelve a medir el resultado, independientemente de la transformación.

**3. ¿Dónde debemos trabajar?** Lee `src/validate.py` y los archivos bajo `sql/validations/` desde VS Code.

**4. Implementación.** El módulo define cada prueba por identificador, regla de origen (`R01` a `R15`), severidad, tabla, universo de registros, condición SQL y cantidad de violaciones. Un criterio con cero violaciones se marca `PASS`.

**5. Explicación del código.** `query()` ejecuta el SQL versionado; `add()` registra el conteo y determina `PASS`, `WARNING` o `FAIL`. `validate_clean()` coordina todas las pruebas y escribe informes.

**6. Resultados esperados.** Cada prueba tendrá una fila trazable con un `validation_run_id`.

**7. Comprobación.** Abre el código de `src/validate.py` y verifica que no ejecute `UPDATE`, `DELETE`, `INSERT` ni `CREATE OR REPLACE TABLE` contra `raw` o `clean`. Se permite insertar auditoría.

**8. Problemas frecuentes.** Un resultado `WARNING` es una incidencia pendiente, no necesariamente un error. No corregir montos para forzar una conciliación.

**9. Resultado del paso.** Tenemos un contrato de calidad replicable.

### Política de severidad

| Severidad | Resultado con violaciones | Política |
|---|---|---|
| `CRITICAL` | `FAIL` | Bloquea publicación; investigar antes de continuar al paso de exportación |
| `WARNING` | `WARNING` | Documentar y revisar; no bloquea automáticamente |
| Cualquier prueba con 0 violaciones | `PASS` | Cumple el criterio evaluado |

La fase concluye con `BLOCKED` si existe algún `FAIL` crítico; `PASS_WITH_WARNINGS` si sólo hay advertencias; `PASS` si todo está correcto. El código de salida de la terminal será 1 para `BLOCKED`, permitiendo su uso futuro en automatizaciones.

## Paso 2. Verificar que el entorno está listo

**1. ¿Qué vamos a realizar?** Activar el entorno y comprobar que la base contiene la Fase 4.

**2. ¿Por qué es necesario?** Las pruebas deben usar exactamente los mismos esquemas que fueron transformados, y no una base temporal vacía.

**3. ¿Dónde debemos trabajar?** Terminal integrada de VS Code, en la carpeta raíz `olist-data-quality`.

**4. Implementación:**

```powershell
conda activate olist-quality
python -c "import duckdb; print(duckdb.__version__)"
python -m pytest -q tests/test_phase5.py
```

Antes de ejecutar el módulo de validación, puedes comprobar las tablas:

```powershell
@'
import duckdb

con = duckdb.connect("database/ecommerce.duckdb", read_only=True)

result = con.execute("""
    SELECT table_schema, table_name
    FROM information_schema.tables
    WHERE table_schema IN ('raw', 'clean', 'audit')
    ORDER BY 1, 2
""").fetchall()

print(result)

con.close()
'@ | python -
```

**5. Explicación.** La prueba abre DuckDB en modo lectura para consultar su catálogo. El esquema `raw` contiene originales; `clean` contiene datos transformados; `audit` contiene historial y excepciones.

**6. Resultados esperados.** Las nueve tablas de RAW y las nueve de CLEAN deben estar presentes, junto con las tablas de auditoría creadas en Fase 4. No se anticipa su número de filas.

**7. Comprobación.** Debe existir `database/ecommerce.duckdb`.

**8. Problemas frecuentes.** Si aparece un error de bloqueo del archivo DuckDB, cierra conexiones abiertas desde Jupyter u otros procesos. Si `duckdb` no está instalado, confirma que el intérprete activo sea `olist-quality`.

**9. Resultado.** La base está accesible para la validación.

## Paso 3. Entender y ejecutar los SQL de validación

**1. ¿Qué vamos a realizar?** Aplicar las consultas versionadas en `sql/validations`.

**2. ¿Por qué es necesario?** Al mantener el SQL separado del orquestador, cada criterio es auditable y reutilizable.

**3. ¿Dónde debemos trabajar?** Abrir la carpeta `sql/validations/` en VS Code.

**4. Implementación.** Los archivos incluidos son:

| Archivo | Objetivo | Técnica SQL |
|---|---|---|
| `01_uniqueness.sql` | Repeticiones de claves de negocio | `GROUP BY`, `HAVING`, `SUM(n-1)` |
| `02_required.sql` | Nulos en campos obligatorios | `IS NULL`, `COUNT()` |
| `03_orphans.sql` | Relaciones sin padre | `NOT EXISTS` |
| `04_predicate.sql` | Dominios, vacíos, fechas y estados | `WHERE`, `COUNT()` |
| `05_payments.sql` | Conciliación total por pedido | `SUM`, CTE, `LEFT JOIN` |
| `06_payment_details.sql` | Mostrar ejemplos de diferencias | Agregaciones + filtro |
| `07_iqr.sql` | Detectar outliers sin corregirlos | `QUANTILE_CONT` |

**5. Explicación del código.** Observa la regla de duplicados:

```sql
SELECT COALESCE(SUM(n - 1), 0)::BIGINT AS duplicates
FROM (
    SELECT order_id, order_item_id, COUNT(*) AS n
    FROM clean.order_items
    GROUP BY order_id, order_item_id
    HAVING COUNT(*) > 1
) AS duplicate_groups;
```

Si la misma clave aparece tres veces, hay **dos excedentes**, no tres. Un pedido que tiene varias filas con distintos `order_item_id` es perfectamente válido.

La comprobación de integridad referencial evita joins multiplicadores:

```sql
SELECT COUNT(*) AS huérfanos
FROM clean.order_items i
WHERE i.product_id IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM clean.products p
    WHERE p.product_id = i.product_id
  );
```

La conciliación **no** hace un join directo entre artículos y pagos: primero suma cada grupo por `order_id` y después une los totales. Si un pedido tiene dos pagos y tres artículos, el join directo produciría seis combinaciones y duplicaría importes.

**6. Resultados esperados.** Cada consulta devuelve conteos; el módulo los interpreta con la política de severidad.

**7. Comprobación.** Ejecuta `python -m pytest -q tests/test_phase5.py`.

**8. Problemas frecuentes.** No cambiar `GROUP BY order_id,order_item_id` a `GROUP BY order_id`; tampoco exigir que `geolocation_zip_code_prefix` sea único.

**9. Resultado.** Las reglas son consultas SQL explícitas, no estimaciones de Python.

## Paso 4. Conocer las comprobaciones implementadas

**1. ¿Qué vamos a realizar?** Validar los resultados de las quince actividades de Fase 4, priorizando seguridad del dato.

**2. ¿Por qué es necesario?** Las anomalías deben evaluarse de acuerdo con su gravedad; no todas ameritan eliminar registros.

**3. ¿Dónde debemos trabajar?** `src/validate.py` y `src/transform_schema.py`.

**4. Implementación y 5. explicación.** Se ejecutan estos grupos:

| Grupo | Verificación | Severidad |
|---|---|---|
| V00 | Última ejecución `COMPLETED`, catálogo de 15 reglas y registros de cuarentena | Crítica para historial; advertencia para cuarentena |
| V01–V02 | Presencia de nueve tablas y columnas/tipos correctos | Crítica |
| V03 | `RAW = CLEAN + cuarentena` por tabla | Crítica |
| V04–V05 | Nulos obligatorios, identificadores vacíos/espaciados y vacíos textuales | Crítica para claves; advertencia para textos opcionales |
| V06 | Unicidad de claves de negocio demostradas | Crítica |
| V07 | Montos negativos; outliers IQR | Crítica para negativos; advertencia para IQR |
| V08 | Dominios de identificadores numéricos, puntuaciones y geolocalización | Crítica |
| V09 | Secuencias de fechas y eventos | Crítica para secuencias imposibles; advertencia para límite de envío anómalo |
| V10 | Coherencia estado/evento | Advertencia |
| V11 | Prefijos postales de cinco dígitos | Advertencia |
| V12 | Claves huérfanas y nulos en referencias obligatorias | Crítica; traducciones opcionales son advertencia |
| V13 | Métodos de pago conocidos | Advertencia |
| V14 | Conciliación de pagos por pedido | Advertencia |

**6. Resultados esperados.** Los conteos serán calculados desde tu propia base. La repetición de una revisión o de un registro geográfico no genera un fallo de unicidad automáticamente.

**7. Comprobación.** Busca `V06.KEY.order_items`, `V12.FK.order_items.product_id`, `V14.PAYMENT_DISCREPANT` en el CSV de resultados.

**8. Problemas frecuentes.** Una categoría de producto sin traducción puede requerir revisión, pero no debe inventarse una traducción. Un outlier puede ser una compra legítima de alto precio.

**9. Resultado.** Existe una puerta de aceptación explícita para el siguiente paso de publicación.

## Paso 5. Ejecutar la validación formal

**1. ¿Qué vamos a realizar?** Ejecutar todas las pruebas en secuencia y producir el informe.

**2. ¿Por qué es necesario?** Las consultas aisladas no producen una decisión reproducible ni registran el historial de ejecución.

**3. ¿Dónde debemos trabajar?** Terminal de VS Code, carpeta raíz.

**4. Implementación:**

```powershell
conda activate olist-quality
python -m src.validate
```

También es posible usar otra base o carpeta de reportes:

```powershell
python -m src.validate --db database/ecommerce.duckdb --reports reports/quality_reports
```

**5. Explicación del código.** La función principal `validate_clean()` conecta con DuckDB, busca la última ejecución de transformación terminada, consulta los nueve conjuntos y escribe los resultados en una transacción de auditoría.

**6. Resultados esperados.** Verás mensajes parecidos a `PASS ...`, `WARNING ...`, o `FAIL ...`. **Son ejemplos de estados, no resultados afirmados para Olist.** Terminará con uno de estos mensajes: `[RESULTADO] PASS`, `[RESULTADO] PASS_WITH_WARNINGS` o `[RESULTADO] BLOCKED`.

**7. Comprobación.** Revisa `reports/quality_reports/phase5_validation_report.md`.

**8. Problemas frecuentes.** Si la terminal termina con código 1, no implica necesariamente una excepción del programa: significa que la decisión es `BLOCKED` y el reporte explica por qué. No borres filas ni cambies valores sin investigar su procedencia.

**9. Resultado.** Se evaluó el estado real de CLEAN con una política explícita.

## Paso 6. Interpretar resultados y excepciones

**1. ¿Qué vamos a realizar?** Interpretar el conjunto de resultados y distinguir problemas críticos de casos sospechosos.

**2. ¿Por qué es necesario?** Un pipeline no es confiable sólo porque sus operaciones terminen sin errores.

**3. ¿Dónde debemos trabajar?** `reports/quality_reports/`.

**4. Implementación.** Revisa los archivos generados:

| Archivo | Finalidad |
|---|---|
| `phase5_validation_report.md` | Informe legible y decisión de aprobación |
| `phase5_validation_results.csv` | Tabla de controles con severidad, estado y universo |
| `phase5_validation_summary.json` | Metadatos y resultados para automatización |
| `phase5_payment_exceptions.csv` | Hasta 50 pedidos con pagos pendientes o discrepantes |

**5. Explicación del código.** La columna `violations` representa registros, grupos o diferencias según cada prueba. La columna `population` define su universo; por ejemplo, en una prueba de clave es el número de filas CLEAN de esa tabla. **No sumar violaciones entre reglas como si fueran filas distintas**, porque un registro puede incumplir varios controles.

**6. Resultados esperados.** Se distinguirán pruebas `PASS`, pruebas `WARNING` y pruebas `FAIL`, basadas exclusivamente en SQL ejecutado sobre los datos reales.

**7. Comprobación.** Para ver los controles fallidos desde DuckDB, usa en un notebook:

```python
import duckdb
from src.config import DB_PATH

with duckdb.connect(str(DB_PATH), read_only=True) as con:
    fallos = con.execute("""
        SELECT check_id, table_name, violations, explanation
        FROM audit.validation_results
        WHERE validation_run_id = (
          SELECT validation_run_id FROM audit.validation_runs
          ORDER BY started_at DESC, validation_run_id DESC LIMIT 1
        ) AND status = 'FAIL'
        ORDER BY violations DESC
    """).df()
print(fallos.to_string(index=False))
```

**8. Problemas frecuentes.** Un pago pendiente no significa automáticamente fraude. Puede depender del estado del pedido, descuentos, vales o de los límites de información del dataset.

**9. Resultado.** Las decisiones quedan sustentadas por tablas y resultados observables.

## Paso 7. Ejecutar pruebas automatizadas y comprobar reproducibilidad

**1. ¿Qué vamos a realizar?** Verificar el motor de validación con datos sintéticos controlados.

**2. ¿Por qué es necesario?** Una ejecución sin fallos de SQL no prueba que el pipeline detectaría una anomalía; las pruebas de regresión introducen casos diseñados para fallar.

**3. ¿Dónde debemos trabajar?** Terminal y `tests/test_phase5.py`.

**4. Implementación:**

```powershell
python -m pytest -q tests/test_phase5.py
python -m pytest -q tests
```

**5. Explicación del código.** Las pruebas comprueban que se registran las 15 reglas, que los artículos múltiples legítimos permanecen, que la validación no modifica CLEAN, que una clave foránea huérfana bloquea la aprobación y que ejecutar el validador dos veces sólo añade auditoría histórica.

**6. Resultados esperados.** La terminal mostrará el número real de pruebas aprobadas, fallidas u omitidas. En el entorno Conda configurado, las pruebas que utilizan DuckDB deben poder ejecutarse; no son cifras medidas previamente sobre tu base Olist.

**7. Comprobación.** La suite debe terminar sin pruebas fallidas antes de considerar terminada la fase.

**8. Problemas frecuentes.** `ModuleNotFoundError: src`: ejecutar `python -m pytest` **desde la raíz del proyecto**, no el ejecutable suelto `pytest` desde otra carpeta. `database is locked`: cerrar notebooks u otras conexiones DuckDB.

**9. Resultado.** Las pruebas de regresión evitan que nuevos cambios rompan correcciones previas.

## Paso 8. Consultar el historial de auditoría

**1. ¿Qué vamos a realizar?** Inspeccionar qué validaciones se ejecutaron y sus resultados.

**2. ¿Por qué es necesario?** Necesitamos trazabilidad de resultados y saber si una prueba corresponde a la última transformación confirmada.

**3. ¿Dónde debemos trabajar?** Notebook o consulta en DuckDB.

**4. Implementación:**

```sql
SELECT validation_run_id, transform_run_id, started_at, finished_at,
       status, critical_failures, warning_checks
FROM audit.validation_runs
ORDER BY started_at DESC;
```

```sql
SELECT check_id, rule_id, table_name, severity, status,
       violations, population, explanation
FROM audit.validation_results
WHERE validation_run_id = (
  SELECT validation_run_id FROM audit.validation_runs
  ORDER BY started_at DESC, validation_run_id DESC LIMIT 1
)
ORDER BY severity, status, check_id;
```

**5. Explicación.** `validation_run_id` identifica una ejecución; `transform_run_id` indica qué versión de CLEAN se revisó. Se preservan los históricos aunque la misma validación se vuelva a ejecutar.

**6. Resultados esperados.** Una fila por ejecución en `audit.validation_runs` y muchas filas por control en `audit.validation_results`.

**7. Comprobación.** Dos ejecuciones consecutivas generan identificadores de validación distintos; los datos CLEAN no cambian por ejecutar sólo este módulo.

**8. Problemas frecuentes.** El historial de Fase 5 no sustituye la auditoría de transformaciones ni indica necesariamente quién corrigió una anomalía fuera del pipeline.

**9. Resultado.** Se cuenta con evidencia reproducible y versionada por ejecución.

## Paso 9. Comprobar la aceptación de Fase 5

**1. ¿Qué vamos a realizar?** Evaluar el estado final antes de la Fase 6.

**2. ¿Por qué es necesario?** No se debe publicar un dataset con fallos críticos únicamente para entregar los archivos.

**3. ¿Dónde debemos trabajar?** Informe y terminal.

**4. Implementación.** Revisa `phase5_validation_report.md`, `phase5_validation_results.csv`, los resultados de pytest y las consultas de auditoría.

**5. Explicación.** `PASS` y `PASS_WITH_WARNINGS` permiten continuar con la automatización; `BLOCKED` requiere investigar los fallos y corregir las políticas o los datos con evidencia. Si hay advertencias, documenta sus limitaciones.

**6. Resultados esperados.** Informes con cifras calculadas en tu base, sin estimaciones artificiales.

**7. Comprobación.** **No debe existir una publicación nueva en `data/clean/*.parquet` causada por `src.validate`.** La publicación se incorporará en Fase 7 después de revisar la decisión de calidad.

**8. Problemas frecuentes.** No recomendar reemplazos arbitrarios de fechas, precios, importes ni identificadores. Una cuarentena puede introducir orfandades posteriores que conviene resolver desde la fuente original; la regla de integridad referencial las detectará.

**9. Resultado.** Fase 5 completada cuando los SQL hayan funcionado, el informe exista y todas las pruebas automatizadas estén verificadas; si quedan fallos críticos, la puerta de publicación sigue bloqueada.

## Limitaciones técnicas explícitas

- Los CSV de Kaggle no traen claves `PRIMARY KEY`/`FOREIGN KEY` declaradas en un DDL. Los vínculos se verifican lógicamente según el modelo de fases anteriores.
- El reporte compara el conteo de RAW con CLEAN + cuarentena **de la última transformación**, pero esa comprobación aislada no permite descubrir modificaciones posteriores a RAW que preserven el número de filas. Para garantizar identidad bit a bit habría que contrastar huellas completas tomadas al extraer.
- No se puede probar la exactitud de precios o direcciones sin una fuente externa fiable.
- La conciliación no debe alterar pagos automáticamente. Distinguir los casos sospechosos mediante las reglas del negocio.
- El validador registra advertencias y bloqueos; **no exporta Parquet** ni sustituye la automatización de Fase 6.

## Próximo paso

**Fase 6 — Orquestación del pipeline completo**: una sola entrada `main()`, controles de idempotencia, logging, manejo de errores y dependencias entre etapas. Fase 7 añadirá la exportación Parquet *condicionada a pruebas críticas aprobadas* y el informe comparativo final RAW vs CLEAN.
