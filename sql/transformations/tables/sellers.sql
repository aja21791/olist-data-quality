-- Fase 4 | sellers: tipificar y normalizar RAW sin alterar originales.
-- Ejecuta Python en CREATE TEMP TABLE _candidate_<tabla> AS <SELECT>;
-- _src_* permite auditar cada fallo de conversion sin perder el valor original.
SELECT
    to_json(r) AS _raw_json,
    sha256(to_json(r)) AS _raw_hash,
    NULLIF(r."seller_id", '') AS "seller_id", 
    CASE WHEN regexp_full_match(NULLIF(TRIM(r."seller_zip_code_prefix"), ''), '[0-9]{1,5}') THEN LPAD(NULLIF(TRIM(r."seller_zip_code_prefix"), ''), 5, '0') ELSE NULLIF(TRIM(r."seller_zip_code_prefix"), '') END AS "seller_zip_code_prefix", 
    LOWER(NULLIF(regexp_replace(TRIM(r."seller_city"), '[[:space:]]+', ' ', 'g'), '')) AS "seller_city", 
    UPPER(NULLIF(regexp_replace(TRIM(r."seller_state"), '[[:space:]]+', ' ', 'g'), '')) AS "seller_state"
FROM raw."sellers" AS r;
