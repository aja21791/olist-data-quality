-- Fase 4 | customers: tipificar y normalizar RAW sin alterar originales.
-- Ejecuta Python en CREATE TEMP TABLE _candidate_<tabla> AS <SELECT>;
-- _src_* permite auditar cada fallo de conversion sin perder el valor original.
SELECT
    to_json(r) AS _raw_json,
    sha256(to_json(r)) AS _raw_hash,
    NULLIF(r."customer_id", '') AS "customer_id", 
    NULLIF(r."customer_unique_id", '') AS "customer_unique_id", 
    CASE WHEN regexp_full_match(NULLIF(TRIM(r."customer_zip_code_prefix"), ''), '[0-9]{1,5}') THEN LPAD(NULLIF(TRIM(r."customer_zip_code_prefix"), ''), 5, '0') ELSE NULLIF(TRIM(r."customer_zip_code_prefix"), '') END AS "customer_zip_code_prefix", 
    LOWER(NULLIF(regexp_replace(TRIM(r."customer_city"), '[[:space:]]+', ' ', 'g'), '')) AS "customer_city", 
    UPPER(NULLIF(regexp_replace(TRIM(r."customer_state"), '[[:space:]]+', ' ', 'g'), '')) AS "customer_state"
FROM raw."customers" AS r;
