-- Los TRY_CAST son necesarios porque RAW retiene las fechas como VARCHAR.
-- Una ausencia NO se convierte artificialmente en cronología válida o falsa.
WITH parsed AS (
  SELECT {purchase_expr} AS purchase_at,
         {approval_expr} AS approved_at,
         {carrier_expr} AS carrier_at,
         {delivered_expr} AS delivered_at
  FROM {layer}."orders"
)
SELECT COUNT(*) AS orders_count,
       COUNT(*) FILTER (WHERE approved_at < purchase_at) AS approval_before_purchase,
       COUNT(*) FILTER (WHERE carrier_at < purchase_at) AS carrier_before_purchase,
       COUNT(*) FILTER (WHERE delivered_at < purchase_at) AS delivery_before_purchase,
       COUNT(*) FILTER (WHERE delivered_at < carrier_at) AS delivery_before_carrier
FROM parsed;
