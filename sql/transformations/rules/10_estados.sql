-- Actividad 10: contradicciones condicionadas por el estado de pedido.
SELECT COUNT(*) AS status_alert_rows FROM clean.orders
WHERE (order_status = 'delivered' AND order_delivered_customer_date IS NULL)
   OR (order_status IN ('created', 'approved') AND order_delivered_customer_date IS NOT NULL)
   OR (order_status NOT IN ('created','approved','invoiced','processing','shipped',
                            'delivered','unavailable','canceled') OR order_status IS NULL);
