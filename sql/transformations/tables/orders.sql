-- Fase 4 | orders: tipificar y normalizar RAW sin alterar originales.
-- Ejecuta Python en CREATE TEMP TABLE _candidate_<tabla> AS <SELECT>;
-- _src_* permite auditar cada fallo de conversion sin perder el valor original.
SELECT
    to_json(r) AS _raw_json,
    sha256(to_json(r)) AS _raw_hash,
    NULLIF(r."order_id", '') AS "order_id", 
    NULLIF(r."customer_id", '') AS "customer_id", 
    LOWER(NULLIF(regexp_replace(TRIM(r."order_status"), '[[:space:]]+', ' ', 'g'), '')) AS "order_status", 
    TRY_CAST(NULLIF(TRIM(r."order_purchase_timestamp"), '') AS TIMESTAMP) AS "order_purchase_timestamp", 
    TRY_CAST(NULLIF(TRIM(r."order_approved_at"), '') AS TIMESTAMP) AS "order_approved_at", 
    TRY_CAST(NULLIF(TRIM(r."order_delivered_carrier_date"), '') AS TIMESTAMP) AS "order_delivered_carrier_date", 
    TRY_CAST(NULLIF(TRIM(r."order_delivered_customer_date"), '') AS TIMESTAMP) AS "order_delivered_customer_date", 
    TRY_CAST(NULLIF(TRIM(r."order_estimated_delivery_date"), '') AS TIMESTAMP) AS "order_estimated_delivery_date", 
    r."order_purchase_timestamp" AS "_src_order_purchase_timestamp",
    r."order_approved_at" AS "_src_order_approved_at",
    r."order_delivered_carrier_date" AS "_src_order_delivered_carrier_date",
    r."order_delivered_customer_date" AS "_src_order_delivered_customer_date",
    r."order_estimated_delivery_date" AS "_src_order_estimated_delivery_date"
FROM raw."orders" AS r;
