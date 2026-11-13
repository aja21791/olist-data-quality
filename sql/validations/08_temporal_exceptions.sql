-- Fase 6: eventos temporales anómalos, sin modificar ni descartar filas CLEAN.
-- Cada anomalía se representa por (order_id, tipo_anomalia), no por pedido solamente.
WITH orders AS (
    SELECT order_id, order_status, order_purchase_timestamp, order_approved_at,
           order_delivered_carrier_date, order_delivered_customer_date
    FROM clean.orders
)
SELECT *,
       'TRANSPORTISTA_ANTES_COMPRA' AS tipo_anomalia,
       DATEDIFF('second', order_purchase_timestamp, order_delivered_carrier_date) / 60.0
           AS desfase_minutos_exacto
FROM orders
WHERE order_delivered_carrier_date IS NOT NULL
  AND order_purchase_timestamp IS NOT NULL
  AND order_delivered_carrier_date < order_purchase_timestamp
UNION ALL
SELECT *,
       'ENTREGA_ANTES_TRANSPORTISTA' AS tipo_anomalia,
       DATEDIFF('second', order_delivered_carrier_date, order_delivered_customer_date) / 60.0
           AS desfase_minutos_exacto
FROM orders
WHERE order_delivered_carrier_date IS NOT NULL
  AND order_delivered_customer_date IS NOT NULL
  AND order_delivered_customer_date < order_delivered_carrier_date
ORDER BY order_id, tipo_anomalia;
