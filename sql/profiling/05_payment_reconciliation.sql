-- Fase 3: suma items y pagos por separado ANTES de unirlos.
-- No multiplicar los importes cruzando artículos x pagos.
WITH items AS (
    SELECT order_id, COUNT(*) AS item_rows,
           SUM(TRY_CAST(NULLIF(TRIM(price),'') AS DECIMAL(18,2))) AS item_price,
           SUM(TRY_CAST(NULLIF(TRIM(freight_value),'') AS DECIMAL(18,2))) AS freight,
           SUM(CASE WHEN NULLIF(TRIM(price),'') IS NOT NULL
                         AND TRY_CAST(NULLIF(TRIM(price),'') AS DECIMAL(18,2)) IS NULL
                    OR NULLIF(TRIM(freight_value),'') IS NOT NULL
                         AND TRY_CAST(NULLIF(TRIM(freight_value),'') AS DECIMAL(18,2)) IS NULL
                    THEN 1 ELSE 0 END) AS invalid_item_rows
    FROM raw.order_items
    WHERE NULLIF(TRIM(order_id),'') IS NOT NULL
    GROUP BY order_id
), payments AS (
    SELECT order_id, COUNT(*) AS payment_rows,
           SUM(TRY_CAST(NULLIF(TRIM(payment_value),'') AS DECIMAL(18,2))) AS paid,
           SUM(CASE WHEN NULLIF(TRIM(payment_value),'') IS NOT NULL
                         AND TRY_CAST(NULLIF(TRIM(payment_value),'') AS DECIMAL(18,2)) IS NULL
                    THEN 1 ELSE 0 END) AS invalid_payment_rows
    FROM raw.order_payments
    WHERE NULLIF(TRIM(order_id),'') IS NOT NULL
    GROUP BY order_id
), base_orders AS (
    SELECT order_id FROM raw.orders WHERE NULLIF(TRIM(order_id),'') IS NOT NULL
    UNION
    SELECT order_id FROM items
    UNION
    SELECT order_id FROM payments
), merged AS (
    SELECT b.order_id,
           i.item_rows, p.payment_rows, i.item_price, i.freight, p.paid,
           COALESCE(i.invalid_item_rows,0) AS invalid_item_rows,
           COALESCE(p.invalid_payment_rows,0) AS invalid_payment_rows,
           i.item_price + i.freight AS items_plus_freight,
           (i.item_price + i.freight) - p.paid AS difference
    FROM base_orders b
    LEFT JOIN items i ON b.order_id = i.order_id
    LEFT JOIN payments p ON b.order_id = p.order_id
)
SELECT *,
       CASE WHEN item_rows IS NULL THEN 'NO_ITEMS'
            WHEN payment_rows IS NULL THEN 'NO_PAYMENTS'
            WHEN invalid_item_rows > 0 OR invalid_payment_rows > 0 THEN 'INVALID_VALUES'
            WHEN items_plus_freight IS NULL OR paid IS NULL THEN 'INCOMPLETE_AMOUNTS'
            WHEN ABS(difference) <= 0.01 THEN 'MATCHED'
            ELSE 'REVIEW_DIFFERENCE' END AS reconciliation_status
FROM merged;
