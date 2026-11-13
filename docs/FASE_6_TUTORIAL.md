# Fase 6 — Automatización y orquestación del pipeline ETL

**Proyecto:** Calidad de datos de Olist  
**Entorno:** Windows 11, Miniconda `olist-quality`, VS Code, Python y DuckDB  
**Entrada:** 9 CSV originales, configuración, SQL y scripts de las fases 1–5.  
**Salidas:** informes y auditoría de ejecución; **todavía NO Parquet**.  
**Estado de referencia verificado para tus archivos del 13/11/2026:** Fase 5 `BLOCKED`: 166 anomalías `carrier_before_purchase`, 23 `delivery_before_carrier`, siete controles `WARNING`. **Todas las 189 revisiones están pendientes.**

## Arquitectura obligatoria

```text
CSV Olist originales (sin edición)
          |
          v
Extracción Python + DuckDB  [src.extract]
          |
          v
RAW (9 tablas persistentes)
          |
          +--- Diagnóstico inicial [src.diagnose]
          |
          v
15 reglas SQL [src.transform + sql/transformations]
          |
          v
CLEAN (9 tablas persistentes)
          |
          v
Pruebas formales [src.validate]
          |
          v
Evaluación documental de anomalías [src.exception_review]
          |
          v
Puerta de calidad [src.pipeline]
     /                 \
  BLOCKED             candidato para Fase 7
     |                       |
  NO PUBLICAR              NO PUBLICAR AÚN
  (seguir revisando)     (Fase 7 debe verificar y publicar)
```

No se crea una capa STAGING persistente. `audit.pipeline_runs`, `audit.pipeline_steps` y `audit.temporal_review_assessments` son artefactos de control, no capas de datos. El comando no llama nunca a `COPY ... TO ...parquet`.

---

## Paso 1. Preparar la carpeta actual sin perder tu trabajo

**1. ¿Qué vamos a realizar?** Integrar los módulos de Fase 6 en el proyecto existente.

**2. ¿Por qué es necesario?** Ya completaste las fases 1–4 y ejecutaste la 5 sobre tu propia base Olist. El proyecto debe reutilizar los mismos scripts, CSV y DuckDB sin reemplazarlos.

**3. ¿Dónde debemos trabajar?** En la carpeta raíz `olist-data-quality`, desde Explorador de Windows y la terminal integrada de VS Code.

**4. Implementación.**

1. Cierra las conexiones abiertas a DuckDB (notebooks, exploradores SQL y otras terminales).
2. Copia `database/ecommerce.duckdb` a una carpeta de respaldo fuera del repositorio.
3. Extrae **solo el parche Fase 6** sobre la raíz del proyecto, respetando la estructura de subcarpetas y aceptando reemplazar `README.md` y el notebook. Si ya modificaste esos documentos, fusiona sus cambios en lugar de sobrescribirlos.
4. **Nunca reemplaces `data/raw/*.csv`, `database/ecommerce.duckdb` o el CSV de revisiones**. El parche no contiene esas entradas.
5. Ejecuta:

```powershell
conda activate olist-quality
python -c "import duckdb; print('DuckDB:', duckdb.__version__)"
python -m pytest -q tests/test_phase6.py
```

**5. Explicación.** El parche agrega `src/pipeline.py`, `src/exception_review.py`, una consulta SQL, pruebas y documentación. La conexión a la base sigue usando `src.config.DB_PATH`.

**6. Resultados esperados.** Las pruebas puras deben ejecutarse sin importar Olist, y las pruebas de integración con DuckDB se ejecutan en tu Windows.

**7. Comprobación.** Deben existir `src/pipeline.py` y `sql/validations/08_temporal_exceptions.sql`.

**8. Problemas frecuentes.** `ModuleNotFoundError: src` suele indicar que la terminal no se encuentra en la raíz del proyecto. Si `database is locked`, cierra Jupyter o el editor que mantiene abierta la conexión.

**9. Resultado del paso.** Los módulos están instalados sin perder el estado anterior.

---

## Paso 2. Comprender el orden de ejecución y las garantías

**1. ¿Qué vamos a realizar?** Analizar cómo opera un *orquestador*.

**2. ¿Por qué es necesario?** Ejecutar comandos sueltos no garantiza que se respete el orden ni que se registre qué etapa falló.

**3. ¿Dónde debemos trabajar?** Abrir `src/pipeline.py` en VS Code.

**4. Implementación.** El programa usa estas funciones existentes, en el orden oficial:

```python
load_raw()                  # CSV -> RAW
# diagnóstico inicial de RAW
# diagnose_raw()
transform_data()            # 15 reglas SQL -> CLEAN
validate_clean()            # pruebas sobre CLEAN
assess_temporal_exceptions() # contrastar eventos con revisiones
# decide_release_gate()    # dictamen, sin publicar
```

La lista íntegra se define en `run_pipeline()` y `--validate-only` ejecuta solamente validación, revisión y decisión.

**5. Explicación.** Cada etapa deja un registro en `audit.pipeline_steps`, con identificador global, inicio, fin, duración, estado y mensaje de error. La ejecución global se registra en `audit.pipeline_runs`.

**6. Resultados esperados.** Aparecerán eventos `INICIO` / `OK` para cada etapa terminada. Un error técnico detiene las siguientes etapas y marca la ejecución `FAILED`.

**7. Comprobación.** Al finalizar, consulta:

```sql
SELECT run_id, status, mode, started_at, finished_at, critical_failures,
       unresolved_critical_events
FROM audit.pipeline_runs
ORDER BY started_at DESC LIMIT 5;

SELECT step_name, status, elapsed_seconds, message
FROM audit.pipeline_steps
WHERE run_id = (SELECT run_id FROM audit.pipeline_runs
                ORDER BY started_at DESC LIMIT 1)
ORDER BY started_at;
```

**8. Problemas frecuentes.** `BLOCKED` no significa que Python falló: indica un resultado técnico válido de las pruebas de calidad. El proceso devuelve código `2` para distinguirlo del error técnico (`1`).

**9. Resultado del paso.** El ETL deja de depender de ejecutar módulos manualmente en orden.

---

## Paso 3. Ejecutar el pipeline completo

**1. ¿Qué vamos a realizar?** Ejecutar CSV → RAW → diagnóstico → CLEAN → pruebas → revisión → puerta de control con un solo comando.

**2. ¿Por qué es necesario?** La ejecución integral debe ser reproducible. Ante fuentes idénticas, la extracción detecta los hashes SHA-256 y reutiliza las tablas RAW; la transformación reconstruye CLEAN sin duplicar registros. El historial de auditoría crece legítimamente con nuevas ejecuciones.

**3. ¿Dónde debemos trabajar?** Terminal PowerShell de VS Code desde la raíz del proyecto.

**4. Implementación.**

```powershell
conda activate olist-quality
python -m src.pipeline --no-figures
```

Si deseas volver a generar las gráficas del diagnóstico:

```powershell
python -m src.pipeline
```

**5. Explicación.** `--no-figures` evita regenerar las visualizaciones, no omite diagnósticos. No se usa `--force-reload`: si un CSV cambia, la extracción detendrá el proceso hasta que investigues la causa.

**6. Resultados esperados.** Con el estado real que compartiste, lo esperable es `BLOCKED`, porque hay 189 eventos aún sin aprobación formal. El comando deberá generar `phase6_pipeline_report.md` y terminar con código `2`. Esto confirma el funcionamiento del bloqueo, no un defecto del ETL.

**7. Comprobación.** Inspecciona `reports/quality_reports/phase6_pipeline_report.md` y `phase6_pipeline_summary.json`. Verifica también que `data/clean` no incluya nuevos Parquet publicados.

**8. Problemas frecuentes.** Si no aparecen los nueve CSV, verifica `data/raw`. Si falla una transformación SQL, observa cuál fue la última etapa `COMPLETED` en `audit.pipeline_steps` y corrige la causa; no elimines filas para conseguir un resultado verde.

**9. Resultado del paso.** El proceso completo queda ejecutado y auditado, aunque la publicación permanezca bloqueada.

---

## Paso 4. Gestionar las 189 anomalías temporales

**1. ¿Qué vamos a realizar?** Crear o reutilizar un registro de **eventos** temporales, no de simples pedidos.

**2. ¿Por qué es necesario?** Los 166 casos de transportista antes de compra y 23 de entrega antes de transportista tienen patrones distintos. Los 189 eventos no constituyen permiso para corregir fechas ni para descartarlos. Algunos pueden ser anomalías de registro de origen.

**3. ¿Dónde debemos trabajar?** En `reports/quality_reports/phase5_temporal_review_register.csv` y el módulo `src/exception_review.py`.

**4. Implementación.** Ejecuta sin volver a transformar:

```powershell
python -m src.exception_review
```

Si el CSV de revisión no existe, se genera con `estado_revision=PENDIENTE` para todos los eventos actuales. Si existe, **no se sobrescribe**, aunque tenga aprobaciones manuales. Puedes copiar el registro que te proporcioné anteriormente a esta ruta antes de ejecutar.

El responsable revisará los campos:

| Columna | Función |
|---|---|
| `estado_revision` | `PENDIENTE`, `RECHAZADA` o `APROBADA` |
| `resolucion` | Para aceptación documental: `EXCEPCION_ACEPTADA` |
| `evidencia_fuente` | Referencia verificable a documento/ticket/log con evidencia |
| `revisado_por` | Responsable real de aceptar el riesgo |
| `fecha_revision` | Fecha ISO 8601 con zona horaria: `AAAA-MM-DDTHH:MM:SS-06:00` |
| `comentarios_revision` | Justificación del tratamiento y alcance |

**No marques las 189 filas como APROBADA por defecto.** Una aprobación válida exige que los campos documentales estén completos, que la fecha no sea futura y que el evento siga correspondiendo a sus valores originales. Se comprueba una huella SHA-256 del pedido, tipo de anomalía, estado y fechas.

**5. Explicación.** `sql/validations/08_temporal_exceptions.sql` identifica los eventos sobre CLEAN sin `UPDATE` ni `DELETE`. Python compara los eventos con el CSV manual. `audit.temporal_review_assessments` conserva el resultado de cada control. Las aprobaciones son decisiones humanas documentales, no una reparación o verificación automática de la fuente.

**6. Resultados esperados.** `phase6_temporal_review_summary.json` contiene totales pendientes, aprobados, rechazados, inválidos y registros obsoletos. `phase6_temporal_decisions.csv` detalla la decisión efectiva por evento.

**7. Comprobación.** Ante tu registro actual, deben quedar **189 pendientes y 0 aprobados**, si la base no ha cambiado. Verifica que el CSV de revisión no sea sobrescrito al repetir el comando.

**8. Problemas frecuentes.** Si `APROBADA` carece de evidencia o fecha, pasa a `INVALIDA`; si cambia una fecha en CLEAN, la aprobación anterior es inválida; si hay eventos nuevos que no figuran en el CSV, se consideran pendientes sin alterar aprobaciones previas. Cualquier revisión obsoleta bloquea el dictamen hasta reconciliarla.

**9. Resultado del paso.** La revisión humana queda separada del diagnóstico y de la transformación automática.

---

## Paso 5. Interpretar la puerta de publicación sin falsear la Fase 5

**1. ¿Qué vamos a realizar?** Comparar fallos críticos originales, eventos aprobados documentalmente y eventos no resueltos.

**2. ¿Por qué es necesario?** Si se cambia un `FAIL` por un `PASS` sin guardar el incumplimiento, el proyecto pierde trazabilidad. La aceptación de una excepción no convierte la secuencia temporal original en correcta.

**3. ¿Dónde debemos trabajar?** `src/pipeline.py`, función `decide_release_gate()` y `phase6_pipeline_report.md`.

**4. Implementación.** El programa analiza por separado estos controles:

- `V09.TIME.carrier_before_purchase`
- `V09.TIME.delivery_before_carrier`

El número de anomalías medido por SQL debe coincidir exactamente con los conteos de Fase 5. Las excepciones documentales sólo afectan al dictamen preparatorio de Fase 6, **no se editan los resultados crudos de la Fase 5**. Otros controles críticos nunca reciben excepciones automáticas.

**5. Explicación.** `phase5_validation_results.csv` mantiene las violaciones originales; `phase6_temporal_decisions.csv` registra su tratamiento. Por eso la fase 6 puede emitir `READY_FOR_PHASE7_WITH_EXCEPTIONS` únicamente si están documentados todos los eventos y no hay otros fallos críticos, mientras la Fase 5 original puede conservar `BLOCKED` como diagnóstico factual. Los dos estados cumplen funciones distintas.

**6. Resultados esperados.** Con 189 pendientes, `BLOCKED`. Con evidencia y aceptación válida de todos los eventos y sin otros fallos, `READY_FOR_PHASE7_WITH_EXCEPTIONS`. Estos son estados de evaluación, **no exportaciones**.

**7. Comprobación.** Inspecciona en `phase6_pipeline_summary.json`: `phase5_raw_critical_failed_checks`, `formally_accepted_temporal_events`, `unresolved_critical_events` y `unexempted_critical_checks`.

**8. Problemas frecuentes.** Si un conteo no coincide entre el SQL de excepciones y las pruebas, la puerta se bloquea. No cambies los nombres de las validaciones ni los predicados para que “encajen”.

**9. Resultado del paso.** La puerta decide con evidencia registrada, sin ocultar las anomalías ni autorizar todavía Parquet.

---

## Paso 6. Revalidar sin reconstruir RAW o CLEAN

**1. ¿Qué vamos a realizar?** Probar una nueva revisión manual sin repetir la transformación.

**2. ¿Por qué es necesario?** No es útil reconstruir 1,000,163 filas geográficas y todo el ETL cada vez que se completa la revisión de una excepción.

**3. ¿Dónde debemos trabajar?** Terminal VS Code.

**4. Implementación:**

```powershell
python -m src.pipeline --validate-only
python -m pytest -q tests
```

**5. Explicación.** La primera instrucción vuelve a ejecutar los controles de Fase 5, la conciliación con el CSV de revisiones y el dictamen preparatorio. No vuelve a importar CSV ni a crear CLEAN.

**6. Resultados esperados.** Nuevas filas de `audit.validation_runs`, `audit.pipeline_runs` y `audit.temporal_review_assessments`, pero la cantidad de filas de RAW y CLEAN permanece igual.

**7. Comprobación.** Compara conteos de todas las tablas y comprueba que el reporte indique modo `VALIDATE_ONLY` en `audit.pipeline_runs`.

**8. Problemas frecuentes.** `exit code 2` en `BLOCKED` es esperado mientras haya decisiones pendientes; `exit code 1` corresponde a un fallo técnico.

**9. Resultado del paso.** Podemos revisar, corregir políticas documentadas y volver a validar sin reprocesar el dataset.

---

## Paso 7. Repetibilidad, fallos y límites de la Fase 6

**1. ¿Qué vamos a realizar?** Verificar que dos ejecuciones completas sobre los mismos CSV no dupliquen datos.

**2. ¿Por qué es necesario?** Un pipeline profesional debe producir resultados estables para entradas y reglas estables; la auditoría, en cambio, registra cada nueva ejecución.

**3. ¿Dónde debemos trabajar?** Terminal y los reportes de auditoría.

**4. Implementación:**

```powershell
python -m src.pipeline --no-figures
python -m src.pipeline --no-figures
```

Consulta:

```sql
SELECT table_name, row_count, source_sha256
FROM audit.raw_load_manifest ORDER BY table_name;

SELECT run_id, status, started_at, finished_at
FROM audit.pipeline_runs ORDER BY started_at DESC LIMIT 2;

SELECT run_id, status, started_at, finished_at
FROM audit.transform_runs ORDER BY started_at DESC LIMIT 2;
```

**5. Explicación.** El extractor reutiliza fuentes idénticas y el transformador crea/reemplaza CLEAN transaccionalmente. Una segunda ejecución debe mantener conteos y reglas iguales salvo variaciones explícitas en fuentes o código. Los IDs, fechas y registros de auditoría sí deben ser diferentes.

**6. Resultados esperados.** Mismas cantidades de RAW/CLEAN y mismas incidencias, nuevos registros históricos en AUDIT.

**7. Comprobación.** Revisa `phase6_pipeline_summary.json`, historial de auditoría y que los CSV originales conserven sus hashes.

**8. Problemas frecuentes.** Si el proceso se interrumpe después de transformar, la auditoría debe reflejar el paso fallido y el pipeline no debe continuar a publicación. Un error de disco no puede impedir siempre una escritura parcial de reportes, por lo que la coherencia transaccional de los CSV publicados se abordará en Fase 7.

**9. Resultado del paso.** La orquestación está lista para revisión académica, sin declarar aprobada la calidad ni publicados los Parquet.

---

## Archivos de Fase 6

| Archivo | Propósito |
|---|---|
| `src/pipeline.py` | Orquestación única, ejecución y auditoría, códigos de salida |
| `src/exception_review.py` | Registro humano, conciliación y huellas de eventos temporales |
| `sql/validations/08_temporal_exceptions.sql` | Identificación independiente de las anomalías temporales |
| `tests/test_phase6.py` | Pruebas de gate, estados, desactualización y orden de ejecución |
| `phase6_pipeline_report.md` | Resumen legible de la orquestación |
| `phase6_pipeline_summary.json` | Resultado estructurado, sin resultados fabricados |
| `phase6_temporal_review_summary.json` | Conteo de revisiones pendientes, aprobadas, inválidas y obsoletas |
| `phase6_temporal_decisions.csv` | Resultado por cada evento |
| `phase5_temporal_review_register.csv` | Archivo de revisión humana (generado sólo si falta) |

## Errores que debemos evitar

- No abrir los CSV originales para escritura ni modificar RAW durante la transformación.
- No cambiar fechas en CLEAN por hipótesis sobre horario o sincronización.
- No aprobar automáticamente filas con diferencias menores de tres horas.
- No considerar `READY_FOR_PHASE7_WITH_EXCEPTIONS` equivalente a que las fechas son verdaderas.
- No exportar Parquet desde la Fase 6. La Fase 7 implementará publicación atómica condicionada, verificación de esquema/conteos y reporte final de calidad.
- No asumir que las pruebas unitarias sustituyen la ejecución del ETL con Olist real.
