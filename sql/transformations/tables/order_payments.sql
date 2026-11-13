-- Fase 4 | order_payments: tipificar y normalizar RAW sin alterar originales.
-- Ejecuta Python en CREATE TEMP TABLE _candidate_<tabla> AS <SELECT>;
-- _src_* permite auditar cada fallo de conversion sin perder el valor original.
SELECT
    to_json(r) AS _raw_json,
    sha256(to_json(r)) AS _raw_hash,
    NULLIF(r."order_id", '') AS "order_id", 
    TRY_CAST(NULLIF(TRIM(r."payment_sequential"), '') AS INTEGER) AS "payment_sequential", 
    LOWER(NULLIF(regexp_replace(TRIM(r."payment_type"), '[[:space:]]+', ' ', 'g'), '')) AS "payment_type", 
    TRY_CAST(NULLIF(TRIM(r."payment_installments"), '') AS INTEGER) AS "payment_installments", 
    TRY_CAST(NULLIF(TRIM(r."payment_value"), '') AS DECIMAL(18,2)) AS "payment_value", 
    r."payment_sequential" AS "_src_payment_sequential",
    r."payment_installments" AS "_src_payment_installments",
    r."payment_value" AS "_src_payment_value"
FROM raw."order_payments" AS r;
