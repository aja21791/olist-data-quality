# Fase 7 — Cierre técnico con política R05 y publicación trazable

> **Actualización de Fase 7**, compatible con las fases 1–7 existentes. Este documento distingue lo que **hace el código** de los resultados que aún deben verificarse ejecutando los nueve CSV reales en Windows 11. No afirma que las anomalías originales sean correctas.

## 1. ¿Qué vamos a realizar?

Cerrar la diferencia entre los criterios de transformación y validación: la regla R05 identificó **189 eventos** cronológicamente contradictorios del dataset Olist (166 transportista antes de compra y 23 entrega antes de transportista). El preflight de Fase 7 devolvía `BLOCKED`, correctamente bajo las reglas anteriores.

**Nueva política técnica R05, versión `R05_CARRIER_TIMESTAMP_UNVERIFIABLE_V1`:** conservar cada pedido, sus precios, pagos y demás atributos; conservar las fechas originales en `raw.orders`; registrar en `audit.temporal_policy_events` la anomalía y el timestamp original; asignar `NULL` a `clean.orders.order_delivered_carrier_date` **sólo** en pedidos donde el timestamp de transportista participa en una cronología imposible; conservar `audit.field_changes`; comprobar la correspondencia entre RAW, CLEAN y AUDIT antes de publicar.

La decisión es deliberadamente conservadora, no una «corrección» del tiempo logístico. Desconocemos cuál sistema de origen produjo el error y **no inferimos una fecha nueva**. Tampoco debe afirmarse que los 189 registros fueron «aprobados» por una persona. Su **investigación de origen puede continuar pendiente**, pero el campo no verificable ya no es utilizado como fecha fiable en CLEAN.

## 2. ¿Por qué es necesario?

Sin un tratamiento reproducible, todo el dataset quedaría bloqueado por 189 eventos, aunque el resto de información de compra pueda ser útil. Simplemente cambiar dos controles `CRITICAL` a `WARNING`, rellenar 189 aprobaciones ficticias o eliminar pedidos no sería aceptable. El tratamiento de R05 se incorpora **antes de la validación** y queda sometido a nuevas pruebas críticas independientes de integridad y trazabilidad.

El hecho de que las validaciones de CLEAN pasen no cambia el histórico: los 189 eventos originales deben permanecer visibles como advertencia `V09.TEMPORAL_SOURCE_FLAGGED` y en los reportes y auditorías.

## 3. ¿Dónde trabajaremos?

En la raíz `olist-data-quality` desde la terminal de VS Code, usando el entorno `olist-quality`. Haz **una copia de seguridad** antes de ejecutar la nueva transformación: `database/ecommerce.duckdb`, `data/clean/` y `reports/quality_reports/phase5_temporal_review_register.csv`. Los nueve CSV originales en `data/raw/` **no deben modificarse**.

### Archivos del parche

| Componente | Implementación |
|---|---|
| `src/temporal_policy.py` | Transacción R05, verificación RAW/CLEAN/AUDIT, vista de calidad |
| `src/transform.py` | Aplica la política en la transacción de Fase 4 y registra las incidencias tratadas |
| `src/validate.py` | Nuevo `V09.TEMPORAL_POLICY_AUDIT` crítico y `V09.TEMPORAL_SOURCE_FLAGGED` de advertencia |
| `src/exception_review.py` | Distingue eventos aún activos de eventos ya tratados sin borrar el registro histórico |
| `src/pipeline.py` | Expone `policy_treated_events` y conserva el gate para errores críticos pendientes |
| `src/release.py` | Publica nueve Parquet junto con un CSV de trazabilidad y huellas SHA-256 |
| `tests/test_temporal_policy.py` | Pruebas para fecha original, supresión controlada, auditoría, manipulación indebida y exportación |
| `tests/test_phase6.py` | Refuerza la prueba de que alterar manualmente CLEAN continúa bloqueando la publicación |

## 4. Instalar y reconstruir CLEAN

Extrae el **parche** en la raíz del proyecto, conservando las rutas relativas `src`, `tests`, `docs` y `notebooks`. No copies encima de la base de datos ni de los CSV originales.

```powershell
conda activate olist-quality
python -m pytest -q tests
python -m src.transform
```

`src.transform` vuelve a leer `raw.*`, reejecuta las 15 reglas, reconstruye `clean.*` dentro de una transacción, y guarda en `audit.temporal_policy_events` el tratamiento efectuado. Es idempotente respecto a los datos: al repetirlo no se acumulan filas en CLEAN; se conserva el historial de cada ejecución en AUDIT.

La cifra esperada para **la misma fuente Olist y versión de reglas** es 189 eventos y 189 pedidos. Si resulta diferente, **revisar** la salida antes de continuar; el código obtiene los valores de los datos reales, no codifica el número 189.

## 5. Verificar la política y sus invariantes

Ejecuta la validación formal:

```powershell
python -m src.validate
```

Revisa `reports/quality_reports/phase5_validation_report.md`. La condición deseada es:

- `V09.TEMPORAL_POLICY_AUDIT`: `PASS`, 0 violaciones. Comprueba la correspondencia con RAW, la fecha original, la supresión en CLEAN y la bitácora por campo.
- `V09.TEMPORAL_SOURCE_FLAGGED`: `WARNING`, con los eventos originales contabilizados. **No son fechas corregidas ni aprobadas por una autoridad externa**.
- `V09.TIME.carrier_before_purchase` y `V09.TIME.delivery_before_carrier`: `PASS` sobre CLEAN porque el timestamp no verificable ya no se ofrece como fecha fiable.
- Los controles de claves, integridad referencial, montos, dominios y demás reglas: sin fallos críticos.

En el notebook, desde la raíz del proyecto:

```python
import duckdb
from src.config import DB_PATH

with duckdb.connect(str(DB_PATH), read_only=True) as con:
    print(con.execute("""
      SELECT carrier_timestamp_quality_status, COUNT(*) AS pedidos
      FROM clean.orders_temporal_quality
      GROUP BY 1 ORDER BY 1
    """).df())
    print(con.execute("""
      SELECT issue_code, COUNT(*) AS eventos
      FROM audit.temporal_policy_events
      WHERE transform_run_id=(
        SELECT run_id FROM audit.transform_runs WHERE status='COMPLETED'
        ORDER BY finished_at DESC NULLS LAST, run_id DESC LIMIT 1)
      GROUP BY 1 ORDER BY 1
    """).df())
```

**`clean.orders_temporal_quality` es una vista, no una tercera capa persistente.** Incluye las columnas normales de `clean.orders` y los indicadores `carrier_timestamp_suppressed_by_quality` y `carrier_timestamp_quality_status` (`QUALITY_SUPPRESSED`, `SOURCE_MISSING` o `USABLE`). Para identificar los `NULL` producidos por calidad, **no** basta mirar `clean.orders`: usa esta vista o el CSV acompañante.

## 6. Publicar los nueve archivos Parquet

Primero comprobar, sin publicar:

```powershell
python -m src.release
```

Si aparece `ELIGIBLE`, solicita la publicación explícita:

```powershell
python -m src.release --publish
```

También puede ejecutarse todo el proceso end-to-end con:

```powershell
python -m src.pipeline --no-figures --publish
```

**Si aparece `BLOCKED`, no omitas el control.** Revisa el informe de Fase 5, `phase6_temporal_review_summary.json` y `phase7_preflight_report.md`. Un nuevo fallo crítico, una auditoría R05 incompleta o un registro de revisión incompatible continúan bloqueando la publicación. El caso de referencia tenía también siete advertencias de otras reglas; su existencia no implica por sí misma un fallo de publicación.

En una publicación permitida, `data/clean/` contendrá **nueve archivos `.parquet`**, además de:

- `temporal_quality_exceptions.csv`: por evento, `order_id`, tipo, timestamps originales y versión de la política. **No es una décima tabla CLEAN ni un décimo Parquet.**
- `release_manifest.json`: conteos, tipos, tamaños y SHA-256 de los nueve archivos, más el CSV auxiliar.
- `quality_report.md`: resumen de las 15 reglas, advertencias y limitaciones.

La exportación verifica nuevamente los nueve Parquet y conserva la versión anterior ante un error recuperable. En Windows, los renombrados consecutivos **no son una transacción atómica ante un apagón**; para sistemas concurrentes se recomiendan publicaciones inmutables versionadas.

## 7. Comprobación integral posterior

```powershell
python -m pytest -q tests
python -m src.verify_release
```

El último comando vuelve a leer cada Parquet y el CSV de trazabilidad, compara SHA-256, conteos, tamaños y columnas, y guarda `phase7_verification_summary.json`. **Debe mostrar `[VERIFIED]` para cerrar la fase.**

```python
from pathlib import Path
import json
import duckdb

folder = Path('data/clean')
manifest = json.loads((folder/'release_manifest.json').read_text(encoding='utf-8'))
assert len(manifest['tables']) == 9
assert len(list(folder.glob('*.parquet'))) == 9

with duckdb.connect() as con:
    for entry in manifest['tables']:
        actual = con.execute('SELECT COUNT(*) FROM read_parquet(?)',
                             [str(folder/entry['file'])]).fetchone()[0]
        assert actual == entry['clean_rows'] == entry['parquet_rows']
        print(entry['table'], actual)

assert manifest['temporal_policy']['file'] == 'temporal_quality_exceptions.csv'
print('Publicación:', manifest['release_id'])
```

La publicación se considera terminada sólo cuando existe un `release_manifest.json` válido y los nueve Parquet pueden abrirse de nuevo con los mismos conteos y esquemas. La aprobación de las pruebas unitarias, por sí sola, no sustituye esta verificación real.

## 8. Problemas frecuentes y límites

- **Sigue apareciendo `BLOCKED` con 189 incidencias:** probablemente no se reconstruyó CLEAN con la nueva política. Ejecuta `python -m src.transform` antes del preflight y revisa que la versión del proyecto esté actualizada.
- **Aparece `V09.TEMPORAL_POLICY_AUDIT = FAIL`:** no edites CLEAN manualmente. Repite la transformación desde RAW después de investigar la causa. Este control identifica anomalías sin justificación documental y modificaciones no reproducibles.
- **El archivo de revisión de Fase 6 aún indica `PENDIENTE`:** es legítimo si la fuente original no ha sido investigada. La decisión `TRATADA_R05` significa únicamente que el timestamp se marcó como no verificable para análisis; no equivale a una aprobación externa.
- **No se importan los módulos:** verifica que la terminal esté en la raíz del proyecto y que el intérprete de VS Code utilice el entorno `olist-quality`.
- **Quedan siete advertencias adicionales:** los outliers IQR, discrepancias de pago, categorías sin traducción y estados faltantes siguen documentados; no se eliminan para forzar un PASS. El número preciso debe leerse del reporte nuevo.
- **El proyecto no está listo para cálculos completos de logística:** los registros con `QUALITY_SUPPRESSED` deben excluirse de métricas que usen la fecha del transportista. Las fechas de entrega y compra se conservan, pero la exactitud externa no se ha validado.
- **La base no está aquí:** esta entrega contiene código y pruebas, pero NO los nueve CSV privados ni los Parquet reales ejecutados en tu equipo.

## 9. Resultado final y criterio de aceptación

Considera la Fase 7 cerrada cuando: pruebas automatizadas sin fallos; transformación repetible de nueve tablas; evidencia de R05 por evento y por campo; ausencia de fallos críticos pendientes; preflight `ELIGIBLE`; publicación `PUBLISHED`; nueve Parquet leídos nuevamente y comparados con CLEAN; manifiesto y reporte final presentes. **La existencia de 189 eventos originales advertidos no demuestra que sus timestamps sean verdaderos.**

Con eso la Fase 8 podrá evaluar los indicadores RAW vs. CLEAN, los resultados y las limitaciones del proyecto sin ocultar anomalías ni declarar una exactitud no comprobada.
