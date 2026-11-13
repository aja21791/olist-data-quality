"""Política R05: invalidar SOLO para uso analítico el evento de transportista
con cronología contradictoria. RAW no cambia; AUDIT conserva el timestamp
original, el estado de las fechas y el motivo del tratamiento.

No supone cuál sistema registró incorrectamente el evento, ni altera entrega,
compra o aprobación. Las inconsistencias originales siguen siendo visibles.
"""
from __future__ import annotations

from .exception_review import EVENT_TYPES, SQL_PATH, make_event_row

POLICY_ID = 'R05_CARRIER_TIMESTAMP_UNVERIFIABLE_V1'


def ensure_temporal_audit(con) -> None:
    con.execute('''CREATE TABLE IF NOT EXISTS audit.temporal_policy_events (
        transform_run_id VARCHAR,
        order_id VARCHAR,
        issue_code VARCHAR,
        check_id VARCHAR,
        order_status VARCHAR,
        order_purchase_timestamp TIMESTAMP,
        order_approved_at TIMESTAMP,
        order_delivered_carrier_date TIMESTAMP,
        order_delivered_customer_date TIMESTAMP,
        minutes_difference DOUBLE,
        source_record_sha256 VARCHAR,
        policy_id VARCHAR,
        applied_at TIMESTAMP
    )''')


def apply_temporal_policy(con, transform_run_id: str) -> dict:
    """Se ejecuta DENTRO de la transacción Fase 4, después de CLEAN.

    La acción para un pedido es única incluso si presenta más de un evento.
    El campo queda NULL por falta de confiabilidad, NO por ausencia en RAW.
    """
    ensure_temporal_audit(con)
    original_sql = SQL_PATH.read_text(encoding='utf-8').strip().rstrip(';')
    con.execute('CREATE OR REPLACE TEMP TABLE _temporal_r05_events AS ' + original_sql)
    # Capturar el valor ANTES de cualquier cambio; auditoría por evento y por campo.
    con.execute('''INSERT INTO audit.temporal_policy_events
       SELECT ?, e.order_id, e.tipo_anomalia,
              CASE e.tipo_anomalia
                WHEN 'TRANSPORTISTA_ANTES_COMPRA' THEN 'V09.TIME.carrier_before_purchase'
                ELSE 'V09.TIME.delivery_before_carrier' END,
              e.order_status, e.order_purchase_timestamp, e.order_approved_at,
              e.order_delivered_carrier_date, e.order_delivered_customer_date,
              CAST(e.desfase_minutos_exacto AS DOUBLE),
              c._raw_hash, ?, current_timestamp
       FROM _temporal_r05_events e
       INNER JOIN _classified_orders c
         ON c.order_id=e.order_id AND c._reason IS NULL''',
        [transform_run_id, POLICY_ID])
    mismatch = int(con.execute('''SELECT
           (SELECT COUNT(*) FROM _temporal_r05_events) -
           (SELECT COUNT(*) FROM audit.temporal_policy_events
            WHERE transform_run_id=?)''', [transform_run_id]).fetchone()[0])
    if mismatch:
        raise RuntimeError('R05: no todos los eventos temporales obtuvieron respaldo de auditoría')
    con.execute('''INSERT INTO audit.field_changes
      SELECT ?, 'orders', 'R05', c._raw_hash, 'order_delivered_carrier_date',
             CAST(c.order_delivered_carrier_date AS VARCHAR), NULL, current_timestamp
      FROM _classified_orders c
      WHERE c._reason IS NULL AND c.order_id IN
            (SELECT DISTINCT order_id FROM _temporal_r05_events)''', [transform_run_id])
    con.execute('''UPDATE clean.orders SET order_delivered_carrier_date=NULL
         WHERE order_id IN (SELECT DISTINCT order_id FROM _temporal_r05_events)''')
    # Vista analítica: distinguir NULL de origen de NULL por política de calidad.
    # La vista no añade una tabla persistente ni modifica el esquema de orders.
    con.execute('''CREATE OR REPLACE VIEW clean.orders_temporal_quality AS
        SELECT o.*,
               (t.order_id IS NOT NULL) AS carrier_timestamp_suppressed_by_quality,
               CASE WHEN t.order_id IS NOT NULL THEN 'QUALITY_SUPPRESSED'
                    WHEN o.order_delivered_carrier_date IS NULL THEN 'SOURCE_MISSING'
                    ELSE 'USABLE' END AS carrier_timestamp_quality_status
        FROM clean.orders o
        LEFT JOIN (
            SELECT DISTINCT order_id FROM audit.temporal_policy_events
            WHERE transform_run_id = (
                SELECT run_id FROM audit.transform_runs WHERE status='COMPLETED'
                ORDER BY finished_at DESC NULLS LAST, run_id DESC LIMIT 1
            )
        ) t ON t.order_id=o.order_id''')
    count_events, count_orders = con.execute('''SELECT COUNT(*), COUNT(DISTINCT order_id)
                                              FROM _temporal_r05_events''').fetchone()
    masked = con.execute('''SELECT COUNT(*) FROM clean.orders o
         JOIN (SELECT DISTINCT order_id FROM _temporal_r05_events) e USING (order_id)
         WHERE o.order_delivered_carrier_date IS NULL''').fetchone()[0]
    if int(masked) != int(count_orders):
        raise RuntimeError('R05: falló la supresión controlada del timestamp no verificable')
    return {'events': int(count_events), 'orders_masked': int(count_orders),
            'policy_id': POLICY_ID}


def get_temporal_policy_state(con, run_id: str | None) -> dict:
    """Comprobación crítica, independiente del chequeo cronológico en CLEAN.

    Compara acciones con la fuente RAW, verifica que el campo suprimido sea NULL,
    y que ningún timestamp transportista confiable se haya modificado.
    """
    if not run_id:
        return {'events': 0, 'orders': 0, 'violations': 1,
                'reason': 'No hay transformación completada'}
    available = con.execute('''SELECT 1 FROM information_schema.tables
      WHERE table_schema='audit' AND table_name='temporal_policy_events' ''').fetchone()
    if not available:
        return {'events': 0, 'orders': 0, 'violations': 1,
                'reason': 'Falta la auditoría de política temporal R05'}
    duplicates = con.execute('''SELECT COUNT(*) FROM (
        SELECT order_id, issue_code FROM audit.temporal_policy_events
        WHERE transform_run_id=? GROUP BY 1,2 HAVING COUNT(*) <> 1
    )''', [run_id]).fetchone()[0]
    # Los eventos registrados deben ser exactamente las anomalías de la fuente,
    # reproducidas con la misma normalización que la tipificación de RAW.
    # El chequeo compara además la fecha que RAW *realmente* proporcionó.
    invalid = con.execute('''SELECT COUNT(*)
      FROM audit.temporal_policy_events a
      LEFT JOIN raw.orders r ON a.order_id=r.order_id
      LEFT JOIN clean.orders c ON a.order_id=c.order_id
      WHERE a.transform_run_id=?
        AND (r.order_id IS NULL OR c.order_id IS NULL
        OR a.policy_id <> ?
        OR a.source_record_sha256 IS NULL
        OR a.source_record_sha256 <> sha256(to_json(r))
        OR a.issue_code NOT IN ('TRANSPORTISTA_ANTES_COMPRA','ENTREGA_ANTES_TRANSPORTISTA')
        OR TRY_CAST(NULLIF(TRIM(r.order_delivered_carrier_date),'') AS TIMESTAMP)
           IS DISTINCT FROM a.order_delivered_carrier_date
        OR TRY_CAST(NULLIF(TRIM(r.order_purchase_timestamp),'') AS TIMESTAMP)
           IS DISTINCT FROM a.order_purchase_timestamp
        OR TRY_CAST(NULLIF(TRIM(r.order_delivered_customer_date),'') AS TIMESTAMP)
           IS DISTINCT FROM a.order_delivered_customer_date
        OR a.order_delivered_carrier_date IS NULL
        OR c.order_delivered_carrier_date IS NOT NULL
        OR NOT ((a.issue_code='TRANSPORTISTA_ANTES_COMPRA'
                  AND a.order_delivered_carrier_date < a.order_purchase_timestamp)
              OR (a.issue_code='ENTREGA_ANTES_TRANSPORTISTA'
                  AND a.order_delivered_customer_date < a.order_delivered_carrier_date)))''',
        [run_id, POLICY_ID]).fetchone()[0]
    # Un NULL en CLEAN que no corresponda a un NULL RAW ni a un evento auditado,
    # o una fecha distinta en un pedido no tratado, indica modificación indebida.
    unexpected = con.execute('''SELECT COUNT(*) FROM clean.orders c
      JOIN raw.orders r USING (order_id)
      WHERE NOT EXISTS (
          SELECT 1 FROM audit.temporal_policy_events a
          WHERE a.transform_run_id=? AND a.order_id=c.order_id)
        AND c.order_delivered_carrier_date IS DISTINCT FROM
            TRY_CAST(NULLIF(TRIM(r.order_delivered_carrier_date),'') AS TIMESTAMP)''',
        [run_id]).fetchone()[0]
    # Detectar un caso que existe en RAW pero no está registrado como acción R05.
    missing = con.execute('''WITH raw_events AS (
      SELECT r.order_id,
          TRY_CAST(NULLIF(TRIM(r.order_purchase_timestamp),'') AS TIMESTAMP) AS p,
          TRY_CAST(NULLIF(TRIM(r.order_delivered_carrier_date),'') AS TIMESTAMP) AS t,
          TRY_CAST(NULLIF(TRIM(r.order_delivered_customer_date),'') AS TIMESTAMP) AS d
      FROM raw.orders r INNER JOIN clean.orders c USING (order_id)
    ), expected AS (
      SELECT order_id, 'TRANSPORTISTA_ANTES_COMPRA' AS issue_code
      FROM raw_events WHERE t < p
      UNION ALL
      SELECT order_id, 'ENTREGA_ANTES_TRANSPORTISTA' AS issue_code
      FROM raw_events WHERE d < t
    )
    SELECT COUNT(*) FROM expected e WHERE NOT EXISTS (
      SELECT 1 FROM audit.temporal_policy_events a
      WHERE a.transform_run_id=? AND a.order_id=e.order_id
      AND a.issue_code=e.issue_code)''', [run_id]).fetchone()[0]
    events, orders = con.execute('''SELECT COUNT(*), COUNT(DISTINCT order_id)
        FROM audit.temporal_policy_events WHERE transform_run_id=?''', [run_id]).fetchone()
    # Asegurar la consistencia entre bitácora de campos y las acciones individuales.
    changes = con.execute('''SELECT COUNT(*) FROM audit.field_changes
        WHERE run_id=? AND source_table='orders' AND rule_id='R05'
          AND column_name='order_delivered_carrier_date' ''', [run_id]).fetchone()[0]
    return {'events': int(events), 'orders': int(orders),
            'violations': int(duplicates + invalid + unexpected + missing + abs(changes - orders)),
            'reason': 'R05: auditoría de eventos, fuente RAW, CLEAN y cambios de campo'}


def load_treated_events(db_path) -> list[dict]:
    """Recupera las 189 incidencias originales, aunque CLEAN ya tenga NULL."""
    import duckdb
    with duckdb.connect(str(db_path), read_only=True) as con:
        exists = con.execute('''SELECT 1 FROM information_schema.tables
             WHERE table_schema='audit' AND table_name='temporal_policy_events' ''').fetchone()
        if not exists:
            return []
        latest = con.execute('''SELECT run_id FROM audit.transform_runs
            WHERE status='COMPLETED'
            ORDER BY finished_at DESC NULLS LAST, run_id DESC LIMIT 1''').fetchone()
        if not latest:
            return []
        result = con.execute('''SELECT order_id, order_status,
               order_purchase_timestamp, order_approved_at,
               order_delivered_carrier_date, order_delivered_customer_date,
               issue_code AS tipo_anomalia, minutes_difference AS desfase_minutos_exacto
               FROM audit.temporal_policy_events WHERE transform_run_id=?
               ORDER BY order_id, issue_code''', [latest[0]])
        columns = [d[0] for d in result.description]
        return [make_event_row(dict(zip(columns, row))) for row in result.fetchall()]
