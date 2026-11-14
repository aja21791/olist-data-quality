# Fase 8 — Línea base confirmada mediante reportes entregados

**Origen de la evidencia:** archivos `release_manifest.json`, `phase7_verification_summary.json`, `phase7_preflight_summary(1).json` y `phase5_validation_report(1).md` proporcionados por el usuario el 13 de noviembre de 2026. **Esta tabla se reconstruyó desde esos artefactos; no sustituye** ejecutar el evaluador contra `database/ecommerce.duckdb` y los Parquet físicos en Windows. Los SHA-256 se contrastaron entre los dos JSON entregados, no se recalcularon contra bytes Parquet ausentes aquí.

| Indicador confirmado en reportes | Resultado |
|---|---|
| Release | `37cef1ab6fc14b3a954f777a74746af4` |
| Transformación | `45b6db3fa1d8430492638ede7b705655` |
| Validación | `ce975d53831b46cb9c77bcd6feba6b0f` |
| Preflight | `ELIGIBLE` |
| Estado de validación | `PASS_WITH_WARNINGS` |
| Publicación | `PUBLISHED` |
| Verificación | `VERIFIED` |
| Parquet | 9 |
| Registros totalizados en 9 tablas (no entidades únicas) | 1,550,922 |
| Tamaño acumulado de archivos `.parquet` (bytes) | 36,263,137 |
| Anomalías temporales R05 de origen | 189 |
| Controles críticos fallidos | 0 |
| Controles con advertencia | 8 |

## Conteo de registros por tabla

| Tabla | RAW | CLEAN | Parquet |
|---|---:|---:|---:|
| `customers` | 99,441 | 99,441 | 99,441 |
| `orders` | 99,441 | 99,441 | 99,441 |
| `order_items` | 112,650 | 112,650 | 112,650 |
| `order_payments` | 103,886 | 103,886 | 103,886 |
| `order_reviews` | 99,224 | 99,224 | 99,224 |
| `products` | 32,951 | 32,951 | 32,951 |
| `sellers` | 3,095 | 3,095 | 3,095 |
| `geolocation` | 1,000,163 | 1,000,163 | 1,000,163 |
| `category_translation` | 71 | 71 | 71 |

## Alcance exacto

- Se observan nueve datasets publicados con recuentos y hashes coincidentes en los reportes proporcionados.
- Los 189 eventos R05 están **gestionados por la política**, no validados por sistemas originales de logística. Deben continuar como limitación.
- Las ocho advertencias requieren interpretación y no son fallos técnicos que deban borrarse.
- No es posible calcular una comparación RAW vs CLEAN de **todas las dimensiones** únicamente desde estos cuatro artefactos: esas métricas se calcularán con `python -m src.evaluate` sobre la base real.
- Para verificar que *cada fila y duplicado* del Parquet coincide con CLEAN, usar `python -m src.evaluate --deep-compare`.
- Ningún resultado de ejecución real de Fase 8 ni cifra de pruebas nuevas se presupone en este anexo.
