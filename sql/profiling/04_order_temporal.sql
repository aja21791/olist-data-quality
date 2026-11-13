-- Fase 3: compara eventos SOLO cuando ambos timestamps pudieron interpretarse.
-- Retraso respecto a fecha estimada se reporta separadamente, no como error.
WITH events AS (
    SELECT order_id, order_status,
           TRY_CAST(NULLIF(TRIM(order_purchase_timestamp), '') AS TIMESTAMP) AS purchase_at,
           TRY_CAST(NULLIF(TRIM(order_approved_at), '') AS TIMESTAMP) AS approved_at,
           TRY_CAST(NULLIF(TRIM(order_delivered_carrier_date), '') AS TIMESTAMP) AS carrier_at,
           TRY_CAST(NULLIF(TRIM(order_delivered_customer_date), '') AS TIMESTAMP) AS delivered_at,
           TRY_CAST(NULLIF(TRIM(order_estimated_delivery_date), '') AS TIMESTAMP) AS estimated_at
    FROM raw.orders
)
SELECT
    COUNT(*) AS orders,
    SUM(CASE WHEN approved_at < purchase_at THEN 1 ELSE 0 END) AS approval_before_purchase,
    SUM(CASE WHEN carrier_at < purchase_at THEN 1 ELSE 0 END) AS carrier_before_purchase,
    SUM(CASE WHEN delivered_at < purchase_at THEN 1 ELSE 0 END) AS delivery_before_purchase,
    SUM(CASE WHEN delivered_at < carrier_at THEN 1 ELSE 0 END) AS delivery_before_carrier,
    SUM(CASE WHEN estimated_at < purchase_at THEN 1 ELSE 0 END) AS estimated_before_purchase,
    SUM(CASE WHEN delivered_at > estimated_at THEN 1 ELSE 0 END) AS delivered_after_estimate,
    SUM(CASE WHEN LOWER(TRIM(order_status)) = 'delivered' AND delivered_at IS NULL
             THEN 1 ELSE 0 END) AS delivered_status_without_delivery_date,
    SUM(CASE WHEN LOWER(TRIM(order_status)) = 'canceled' AND delivered_at IS NOT NULL
             THEN 1 ELSE 0 END) AS canceled_status_with_delivery_date
FROM events;
