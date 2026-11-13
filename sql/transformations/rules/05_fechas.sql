-- Actividad 05: secuencias sospechosas de eventos en pedidos (conservar, advertir).
SELECT COUNT(*) AS temporal_alert_rows FROM clean.orders
WHERE (order_approved_at IS NOT NULL AND order_approved_at < order_purchase_timestamp)
   OR (order_delivered_carrier_date IS NOT NULL AND order_delivered_carrier_date < order_purchase_timestamp)
   OR (order_delivered_customer_date IS NOT NULL AND order_delivered_customer_date < order_purchase_timestamp)
   OR (order_delivered_customer_date IS NOT NULL AND order_delivered_carrier_date IS NOT NULL
       AND order_delivered_customer_date < order_delivered_carrier_date);
