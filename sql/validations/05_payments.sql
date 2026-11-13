-- Fase 5 / R12: totales se agregan separadamente ANTES del JOIN.
WITH items AS (
  SELECT order_id, SUM(price + freight_value) AS item_total
  FROM clean.order_items GROUP BY order_id
), payments AS (
  SELECT order_id, SUM(payment_value) AS paid_total
  FROM clean.order_payments GROUP BY order_id
), order_totals AS (
  SELECT o.order_id, i.item_total, p.paid_total,
         ABS(i.item_total - p.paid_total) AS absolute_difference
  FROM clean.orders o
  LEFT JOIN items i ON o.order_id = i.order_id
  LEFT JOIN payments p ON o.order_id = p.order_id
)
SELECT COUNT(*) AS orders_total,
       COUNT(*) FILTER (WHERE item_total IS NULL OR paid_total IS NULL) AS pending,
       COUNT(*) FILTER (WHERE item_total IS NOT NULL AND paid_total IS NOT NULL
                            AND absolute_difference > 0.01) AS discrepant,
       COUNT(*) FILTER (WHERE item_total IS NOT NULL AND paid_total IS NOT NULL
                            AND absolute_difference <= 0.01) AS reconciled,
       COALESCE(MAX(absolute_difference), 0) AS max_absolute_difference
FROM order_totals;
