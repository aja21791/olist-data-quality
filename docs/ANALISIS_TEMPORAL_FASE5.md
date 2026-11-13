# Análisis de anomalías temporales — Fase 5

Fuente: `phase5_temporal_exceptions.csv`. Total: **189 pedidos únicos**, 166 con fecha de transportista anterior a compra y 23 con entrega anterior a transportista. No existen solapamientos entre estos dos grupos.

## Observaciones verificadas

- De los 166 casos del primer grupo, 164 presentan una anticipación de hasta tres horas; dos son excepcionales (aproximadamente 4.23 y 171.21 días).
- En esos 166 casos, los segundos de la fecha de transportista son `00`, concentrados en compras entre abril y agosto de 2018.
- Los 23 casos del segundo grupo pertenecen a compras de 2016 y 2017, con diferencias entre 23 minutos y 16.1 días (mediana aproximada de 39.9 horas).
- De los 189 registros, 188 constan como `delivered` y uno como `shipped`.
- Esos patrones podrían indicar diferencias de captura o semántica entre sistemas, pero no prueban su causa.

## Tratamiento recomendado

1. Mantener intacta la fuente RAW y no corregir fechas por suposición.
2. Registrar cada incidente con `estado_revision=PENDIENTE`; contrastar con una fuente logística confiable, si existe.
3. Si existe un evento confirmado, documentar la fecha correcta y la evidencia antes de plantear la corrección en CLEAN y su auditoría.
4. Si la fuente original no permite corregir la anomalía, gestionar la decisión formal de una excepción aceptada por un responsable. El reporte debe seguir contando los eventos inconsistentes.
5. Mantener `BLOCKED` para la aprobación de publicación mientras existan eventos críticos sin corregir ni excepción formal aprobada. Es válido desarrollar la automatización de la Fase 6 manteniendo ese bloqueo.
6. Separar las métricas temporales derivadas: los pedidos con secuencias inconsistentes no deben participar en cálculos que supongan cronología válida, sin eliminarlos de CLEAN.

**Importante:** Este registro es un instrumento de revisión, no una aprobación automática. Todos los 189 eventos permanecen pendientes.
