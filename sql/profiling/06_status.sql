-- Fase 3: distribución de estados (ver fechas en 04_order_temporal.sql).
SELECT TRIM(order_status) AS order_status, COUNT(*) AS records_count
FROM raw.orders
GROUP BY 1 ORDER BY records_count DESC, order_status;
