-- Fase 4 | category_translation: tipificar y normalizar RAW sin alterar originales.
-- Ejecuta Python en CREATE TEMP TABLE _candidate_<tabla> AS <SELECT>;
-- _src_* permite auditar cada fallo de conversion sin perder el valor original.
SELECT
    to_json(r) AS _raw_json,
    sha256(to_json(r)) AS _raw_hash,
    LOWER(NULLIF(regexp_replace(TRIM(r."product_category_name"), '[[:space:]]+', ' ', 'g'), '')) AS "product_category_name", 
    LOWER(NULLIF(regexp_replace(TRIM(r."product_category_name_english"), '[[:space:]]+', ' ', 'g'), '')) AS "product_category_name_english"
FROM raw."category_translation" AS r;
