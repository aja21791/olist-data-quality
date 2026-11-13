-- Fase 4 | order_items: tipificar y normalizar RAW sin alterar originales.
-- Ejecuta Python en CREATE TEMP TABLE _candidate_<tabla> AS <SELECT>;
-- _src_* permite auditar cada fallo de conversion sin perder el valor original.
SELECT
    to_json(r) AS _raw_json,
    sha256(to_json(r)) AS _raw_hash,
    NULLIF(r."order_id", '') AS "order_id", 
    TRY_CAST(NULLIF(TRIM(r."order_item_id"), '') AS INTEGER) AS "order_item_id", 
    NULLIF(r."product_id", '') AS "product_id", 
    NULLIF(r."seller_id", '') AS "seller_id", 
    TRY_CAST(NULLIF(TRIM(r."shipping_limit_date"), '') AS TIMESTAMP) AS "shipping_limit_date", 
    TRY_CAST(NULLIF(TRIM(r."price"), '') AS DECIMAL(18,2)) AS "price", 
    TRY_CAST(NULLIF(TRIM(r."freight_value"), '') AS DECIMAL(18,2)) AS "freight_value", 
    r."order_item_id" AS "_src_order_item_id",
    r."shipping_limit_date" AS "_src_shipping_limit_date",
    r."price" AS "_src_price",
    r."freight_value" AS "_src_freight_value"
FROM raw."order_items" AS r;
