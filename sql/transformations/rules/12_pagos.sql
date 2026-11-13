-- Actividad 12: agregar ANTES del JOIN para evitar multiplicaciones N:M.
WITH items AS (
 SELECT order_id, SUM(price+freight_value) AS items_total FROM clean.order_items GROUP BY order_id
), payments AS (
 SELECT order_id, SUM(payment_value) AS payments_total FROM clean.order_payments GROUP BY order_id
), check_orders AS (
 SELECT o.order_id, i.items_total, p.payments_total
 FROM clean.orders o
 LEFT JOIN items i ON i.order_id=o.order_id
 LEFT JOIN payments p ON p.order_id=o.order_id
)
SELECT COUNT(*) FILTER (WHERE items_total IS NOT NULL AND payments_total IS NOT NULL
                               AND ABS(items_total-payments_total)>0.01) AS discrepant_orders,
       COUNT(*) FILTER (WHERE items_total IS NULL OR payments_total IS NULL) AS pending_orders,
       COUNT(*) FILTER (WHERE items_total IS NOT NULL AND payments_total IS NOT NULL
                               AND ABS(items_total-payments_total)<=0.01) AS reconciled_orders
FROM check_orders;
