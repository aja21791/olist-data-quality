# Fase 7 — Publicación controlada de Parquet y reporte de calidad

> **Actualización de cierre de Fase 7:** se implementó una política R05 para eventos temporales contradictorios. El documento que sigue describe el gate original previo a dicho tratamiento y permanece como material histórico. Para reconstruir CLEAN, verificar la auditoría y publicar Parquet con la política actual, sigue [FASE_7_CIERRE_POLITICA_TEMPORAL.md](FASE_7_CIERRE_POLITICA_TEMPORAL.md). Los 189 eventos originales siguen documentados y no se consideran fechas confirmadas.


## Objetivo y alcance

Esta fase completa la salida requerida del pipeline **CSV originales → extracción con Python + DuckDB → RAW → 15 reglas SQL → CLEAN → pruebas de calidad → Parquet + reporte de calidad**. Se mantiene exactamente **dos capas persistentes** (`raw` y `clean`) dentro de DuckDB. `audit` es un conjunto de registros auxiliares y **no** una capa STAGING.

**Condición observada en la ejecución real del curso (13/11/2026):** Fase 5 `BLOCKED`: 166 casos de transportista anterior a la compra y 23 de entrega al cliente anterior al transportista; 189 eventos sin aprobación. En la Fase 6 se demostró el bloqueo; por tanto, **no está permitida la publicación real en este momento**. Este módulo puede instalarse, probarse con datos sintéticos y utilizarse para registrar la decisión de bloqueo. El bloqueo no es un fallo del programa: es la política de calidad funcionando.

El módulo NO elimina ni corrige las 189 fechas por conveniencia. Una aprobación documental tampoco convierte automáticamente las fechas en correctas. Si existen datos confirmados por una fuente logística, su corrección requeriría otra implementación trazable desde RAW a CLEAN y una nueva validación.

## Conceptos previos

- **Prueba crítica**: una comprobación cuyo incumplimiento impide la publicación; por ejemplo, claves inválidas o contradicciones temporales no justificadas.
- **Gate de calidad**: decisión calculada con la validación formal y el registro de revisión de excepciones. Conserva los resultados originales de la Fase 5.
- **Parquet**: formato columnar que conserva tipos, permite compresión ZSTD y lectura selectiva por columnas. A diferencia de CSV, no exige volver a inferir los tipos.
- **Manifest de publicación**: archivo JSON que identifica tablas exportadas, conteos, esquema, bytes y SHA-256, además de los IDs de transformación/validación.
- **SHA-256**: huella de un archivo; permite detectar modificaciones accidentales posteriores. Una huella no demuestra que el dato sea verdadero.
- **Rollback**: restauración de los archivos publicados anteriormente cuando el cambio no termina correctamente.

**Decisión de arquitectura:** para publicar las nueve tablas como `data/clean/*.parquet` (ruta obligatoria del proyecto), se preparan los archivos en una carpeta **temporal de publicación**, se verifican, y después se sustituye el directorio de resultados como una unidad, con copia previa y rollback. Esta carpeta temporal **no es una tercera capa de datos ni una etapa STAGING del ETL**. En Windows, dos renombrados de directorio no forman una transacción atómica perfecta ante una interrupción eléctrica: los consumidores no deben leer el directorio durante la sustitución. Para un servicio concurrente de producción se recomienda publicación mediante versiones inmutables y un puntero transaccional, como evolución futura.

---

## Paso 1: Comprobar la instalación de la fase

**1. ¿Qué vamos a realizar?** Verificar que los nuevos módulos y las dependencias estén disponibles.

**2. ¿Por qué es necesario?** Una publicación parcial o realizada con código incompatible es peor que mantener el bloqueo.

**3. ¿Dónde debemos trabajar?** Terminal integrada de VS Code, carpeta raíz `olist-data-quality`.

**4. Implementación:**

```powershell
conda activate olist-quality
python -c "import duckdb; print('DuckDB',duckdb.__version__)"
python -m pytest -q tests/test_phase7.py
python -m pytest -q tests
```

**5. Explicación del código.** La primera instrucción selecciona el entorno. Las pruebas incluyen casos sintéticos de bloqueo, huellas, autorización, rollback y exportación/lectura real con DuckDB.

**6. Resultados esperados.** Se ejecutan las pruebas, sin crear Parquet reales de Olist. El resultado exacto depende de la instalación local y de versiones compatibles.

**7. Comprobación.** No debe aparecer ninguna prueba fallida; en tu equipo DuckDB está instalado y el caso de integración debe ejecutarse, no omitirse.

**8. Problemas frecuentes.** Si no encuentra `duckdb`, selecciona el intérprete `olist-quality`. Si falla una prueba de Fase 3, verifica que conservaste el parche de reproducibilidad (`ORDER BY orders DESC, reconciliation_status ASC`).

**9. Resultado del paso.** Componentes instalados y pruebas listas para uso.

## Paso 2: Realizar el preflight sin publicar

**1. ¿Qué vamos a realizar?** Ejecutar de nuevo las validaciones de CLEAN y la revisión de excepciones temporales.

**2. ¿Por qué es necesario?** Nunca deben utilizarse resultados antiguos como autorización para publicar contenido que puede haber cambiado.

**3. ¿Dónde debemos trabajar?** Terminal de VS Code.

**4. Implementación:**

```powershell
python -m src.release
```

**5. Explicación del código.** La ejecución revalida CLEAN mediante `src.validate`, reevalúa las excepciones con `src.exception_review` y calcula `decide_release_gate`. **No crea Parquet** porque no se añadió `--publish`.

**6. Resultados esperados.** Para las 189 excepciones pendientes, `[FASE 7] BLOCKED` y código de salida `2`. Los archivos en `reports/quality_reports/` incluyen `phase7_preflight_summary.json` y `phase7_preflight_report.md`.

**7. Comprobación.** Revisa `status`, `reasons`, `validation_run_id`, `unresolved_critical_events` y la huella de revisión. No debe existir un archivo Parquet nuevo por este paso.

**8. Problemas frecuentes.** `BLOCKED` **no** significa que la terminal esté mal configurada: significa que la publicación no es segura de acuerdo con las reglas actuales.

**9. Resultado del paso.** Decisión documentada sin publicación.

## Paso 3: Confirmar que no se publica con errores críticos

**1. ¿Qué vamos a realizar?** Probar explícitamente la solicitud de publicación sobre el estado real bloqueado.

**2. ¿Por qué es necesario?** El requisito más importante del proyecto es que un fallo crítico detenga la exportación.

**3. ¿Dónde debemos trabajar?** Terminal de VS Code.

**4. Implementación:**

```powershell
python -m src.release --publish
```

**5. Explicación del código.** `--publish` expresa intención de publicación, pero **no omite las comprobaciones**. Si existen errores críticos sin resolución o aprobación legítima, el programa devuelve `BLOCKED`.

**6. Resultados esperados.** Para tu dataset actual, continuará bloqueado. No se creará ni reemplazará `data/clean/*.parquet`.

**7. Comprobación.** Ejecuta `Get-ChildItem .\data\clean\*.parquet -ErrorAction SilentlyContinue` para comprobar que no se publicaron archivos por primera vez. Si existía una versión ya aprobada, debe conservarse sin cambios.

**8. Problemas frecuentes.** **No** cambies las validaciones temporales a `WARNING` sólo para obtener un estado verde. **No** rellenes campos de autorización con datos ficticios para pasar el gate.

**9. Resultado del paso.** Bloqueo verificado en la Fase 7.

## Paso 4: Distinguir decisión humana de corrección automática

**1. ¿Qué vamos a realizar?** Documentar cómo se podrían aceptar excepcionalmente eventos que no puedan corregirse desde fuentes confiables.

**2. ¿Por qué es necesario?** El responsable de calidad no debe confundir una incidencia conocida con un dato verificado como exacto.

**3. ¿Dónde debemos trabajar?** `reports/quality_reports/phase5_temporal_review_register.csv`, gestionado por una persona autorizada.

**4. Implementación.** Revisar cada uno de los 189 eventos contra evidencia de la fuente. La aprobación individual exige los campos: `estado_revision=APROBADA`, `resolucion=EXCEPCION_ACEPTADA`, `evidencia_fuente`, `revisado_por`, `fecha_revision` ISO 8601 con zona horaria y `comentarios_revision`. Una revisión pendiente conserva `PENDIENTE`. Ejecuta:

```powershell
python -m src.release
```

**5. Explicación.** El módulo preserva la huella de cada evento. Si cambian las fechas, el estado o el tipo de anomalía, una aprobación anterior deja de ser aplicable.

**6. Resultados esperados.** Sólo las decisiones realmente documentadas pueden considerarse aceptadas. Las 189 incidencias originales seguirán presentes en Fase 5, incluso si eventualmente todas fueran autorizadas.

**7. Comprobación.** Verificar el archivo `phase6_temporal_decisions.csv`; la sumatoria de aceptadas, pendientes, rechazadas e inválidas debe coincidir con el total de eventos detectados.

**8. Problemas frecuentes.** Un número de ticket inventado no es evidencia. El sistema no puede comprobar externamente la identidad de la persona ni validar la fuente logística: requiere responsabilidad humana real.

**9. Resultado del paso.** Política de excepciones gobernada, sin alterar fechas.

## Paso 5: Autorización de publicación independiente (cuando proceda)

**1. ¿Qué vamos a realizar?** Exigir un documento adicional si la publicación pretende conservar excepciones críticas aceptadas.

**2. ¿Por qué es necesario?** Una revisión caso por caso no equivale a una autorización institucional para publicar un dataset con errores históricos conocidos.

**3. ¿Dónde debemos trabajar?** Archivo JSON creado por un responsable de negocio y un responsable de calidad, fuera de los CSV de origen.

**4. Implementación.** Una vez que el gate resulte `READY_FOR_PHASE7_WITH_EXCEPTIONS`, se generará `reports/quality_reports/phase7_authorization_template.json`. Copiarlo como `phase7_authorization.json`, y rellenar manualmente los campos con evidencia legítima:

```json
{
  "decision": "AUTHORIZE_DOCUMENTED_EXCEPTIONS",
  "review_sha256": "COPIAR_SIN_MODIFICAR_LA_HUELLA_REAL_DEL_TEMPLATE",
  "business_owner": "NOMBRE_REAL_DEL_RESPONSABLE_NEGOCIO",
  "quality_owner": "NOMBRE_REAL_DEL_RESPONSABLE_CALIDAD",
  "authorization_reference": "REFERENCIA_REAL_ACTA_O_TICKET",
  "approved_at": "2026-11-13T12:00:00-06:00"
}
```

**Este JSON es un esquema ilustrativo, NO es una autorización válida ni debe copiarse con nombres o fechas inventadas.** Los responsables deben ser personas distintas, la autorización debe tener respaldo externo, la huella debe coincidir y la fecha no puede estar en el futuro.

```powershell
python -m src.release --authorization reports/quality_reports/phase7_authorization.json
```

**5. Explicación.** El hash de revisión vincula la autorización a decisiones específicas. Cambiar una decisión o el evento invalida la autorización.

**6. Resultados esperados.** `ELIGIBLE` exclusivamente si no quedan críticos sin resolver y la autorización es consistente; aún **sin Parquet** porque falta `--publish`.

**7. Comprobación.** Verificar `phase7_preflight_summary.json`, `authorization_required=true`, `status=ELIGIBLE`.

**8. Problemas frecuentes.** El programa comprueba estructura, roles diferentes y coherencia de huellas, pero NO puede verificar identidades, firmas ni autenticidad de la evidencia. La aprobación institucional debe ser real, no sólo sintáctica.

**9. Resultado del paso.** Consentimiento verificable y vinculado a un conjunto específico de revisiones.

## Paso 6: Publicación validada (solo cuando esté autorizada)

**1. ¿Qué vamos a realizar?** Exportar todas las tablas CLEAN a Parquet y verificar su contenido.

**2. ¿Por qué es necesario?** Publicar sin comparar conteos o tipos podría introducir errores nuevos después de superar las validaciones SQL.

**3. ¿Dónde debemos trabajar?** Terminal de VS Code, con responsables ya autorizados cuando haya excepciones.

**4. Implementación:**

```powershell
# Si no hay excepciones críticas: 
python -m src.release --publish

# Si existen excepciones formales válidas y autorización externa real:
python -m src.release --publish --authorization reports/quality_reports/phase7_authorization.json
```

**5. Explicación.** DuckDB utiliza `COPY ... (FORMAT PARQUET, COMPRESSION ZSTD)`. El programa lee de nuevo cada archivo con `read_parquet` y compara `COUNT(*)` y el esquema SQL con CLEAN. Genera SHA-256 y tamaños reales. Sólo entonces cambia `data/clean` con un renombrado controlado, reservando rollback del directorio anterior.

**6. Resultados esperados.** Nueve archivos `data/clean/*.parquet`, `data/clean/release_manifest.json`, `data/clean/quality_report.md` y `reports/quality_reports/phase7_quality_report.md`.

**7. Comprobación:**

```powershell
Get-ChildItem .\data\clean\*.parquet
Get-Content .\data\clean\release_manifest.json
Get-Content .\reports\quality_reports\phase7_quality_report.md
```

**8. Problemas frecuentes.** Si hay otro proceso abierto con archivos Parquet, Windows puede impedir renombrar el directorio. Cierra procesos lectores y vuelve a intentar. Si existe `data/.clean_publication.lock`, investiga una ejecución interrumpida antes de eliminarlo manualmente. No borres respaldos `.clean_previous_*` sin verificar el estado de la publicación.

**9. Resultado del paso.** Parquet y reporte final sólo cuando los datos tienen aprobación efectiva.

## Paso 7: Ejecutar un único pipeline completo

**1. ¿Qué vamos a realizar?** Integrar también la Fase 7 en el comando principal del proyecto.

**2. ¿Por qué es necesario?** El mismo flujo debe funcionar desde un origen nuevo y admitir reejecuciones sin necesidad de ejecutar módulos manualmente fuera de orden.

**3. ¿Dónde debemos trabajar?** Terminal raíz de VS Code.

**4. Implementación:**

```powershell
# Sin publicación: extracción, diagnóstico, limpieza, validación y revisión.
python -m src.pipeline --no-figures

# Pipeline completo con intento de publicación condicionado.
python -m src.pipeline --no-figures --publish
```

Si se requiere autorización externa real, añade `--authorization reports/quality_reports/phase7_authorization.json` a la segunda instrucción.

**5. Explicación.** `src.pipeline` mantiene el orden de las fases; sólo invoca `src.release.publish_validated()` después de la validación y el gate. La exportación no forma parte de RAW ni de la transformación.

**6. Resultados esperados.** El estado actual del proyecto seguirá siendo `BLOCKED`. El comando completo puede tardar más que `src.release` porque repite el diagnóstico y las transformaciones.

**7. Comprobación.** Revisar `phase6_pipeline_report.md` y `phase7_preflight_report.md`. Si se autoriza en el futuro, revisar también el manifest de publicación.

**8. Problemas frecuentes.** Si la ejecución completa finaliza con código `2` por calidad, el código funciona: la condición de publicación no se cumple.

**9. Resultado del paso.** Una única entrada reproducible a todo el flujo ETL, con publicación condicional.

## Paso 8: Lectura independiente y evaluación

**1. ¿Qué vamos a realizar?** Verificar la forma de consumir los datos publicados y la trazabilidad de la entrega.

**2. ¿Por qué es necesario?** Una exportación confiable debe poder reutilizarse sin conocer el código del pipeline.

**3. ¿Dónde debemos trabajar?** Notebook Jupyter o terminal de Python, sólo tras un release aprobado.

**4. Implementación:**

```python
from pathlib import Path
import duckdb
import json

manifest = json.loads(Path('data/clean/release_manifest.json').read_text(encoding='utf-8'))
print(manifest['release_id'], manifest['status'])
con = duckdb.connect()
for table in manifest['tables']:
    file = Path('data/clean') / table['file']
    rows = con.execute('SELECT COUNT(*) FROM read_parquet(?)', [str(file)]).fetchone()[0]
    assert rows == table['clean_rows']
    print(table['table'], rows)
con.close()
```

**5. Explicación.** El lector utiliza la información del manifest y compara el número de registros por archivo; los tipos ya fueron verificados durante la publicación.

**6. Resultados esperados.** Nueve lecturas exitosas cuando la entrega haya sido publicada.

**7. Comprobación.** Comparar el manifest con el informe de calidad y el registro de auditoría `audit.release_runs`.

**8. Problemas frecuentes.** Si no existe `release_manifest.json`, no existe una entrega aprobada. No fabriques un manifest para ocultar un resultado `BLOCKED`.

**9. Resultado del paso.** Entregables finales verificables y reproducibles.

---

## Política de errores y límites

- **Salida `0`:** comprobación elegible o publicación exitosa, según los argumentos.
- **Salida `2`:** bloqueado por reglas de calidad o autorización incompleta. No modifica la publicación previa.
- **Salida `1`:** fallo técnico inesperado; investigar archivo, conexión y bitácora.
- Un lock existente detiene un segundo escritor. Sólo una persona autorizada debe retirar un lock obsoleto después de comprobar que no hay otra publicación activa.
- Reemplazar una carpeta con dos renombrados no es completamente atómico frente a caída eléctrica. Una interrupción a mitad de renombrados exige inspeccionar `data/clean` y `data/.clean_previous_*` antes de reintentar.
- No se considera publicada la Fase 7 únicamente por tener archivos de pruebas sintéticas.
- Con el estado real de 13/11/2026, el resultado conforme es **BLOCKED sin Parquet real**. La Fase 8 puede documentar esta limitación sin declarar éxito en la condición de publicación.
