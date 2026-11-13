-- Detalle de discrepancias, limitado para revisión; no corrige importes.
WITH items AS (
  SELECT order_id, SUM(price + freight_value) AS item_total
  FROM clean.order_items GROUP BY order_id
), payments AS (
  SELECT order_id, SUM(payment_value) AS paid_total
  FROM clean.order_payments GROUP BY order_id
)
SELECT o.order_id, i.item_total, p.paid_total,
       CASE WHEN i.item_total IS NULL OR p.paid_total IS NULL
            THEN 'PENDING' ELSE 'DISCREPANT' END AS situation,
       CASE WHEN i.item_total IS NULL OR p.paid_total IS NULL
            THEN NULL ELSE ABS(i.item_total - p.paid_total) END AS difference
FROM clean.orders o
LEFT JOIN items i ON i.order_id=o.order_id
LEFT JOIN payments p ON p.order_id=o.order_id
WHERE i.item_total IS NULL OR p.paid_total IS NULL
   OR ABS(i.item_total - p.paid_total) > 0.01
ORDER BY o.order_id
LIMIT {limit};
