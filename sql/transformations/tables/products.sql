-- Fase 4 | products: tipificar y normalizar RAW sin alterar originales.
-- Ejecuta Python en CREATE TEMP TABLE _candidate_<tabla> AS <SELECT>;
-- _src_* permite auditar cada fallo de conversion sin perder el valor original.
SELECT
    to_json(r) AS _raw_json,
    sha256(to_json(r)) AS _raw_hash,
    NULLIF(r."product_id", '') AS "product_id", 
    LOWER(NULLIF(regexp_replace(TRIM(r."product_category_name"), '[[:space:]]+', ' ', 'g'), '')) AS "product_category_name", 
    TRY_CAST(NULLIF(TRIM(r."product_name_lenght"), '') AS INTEGER) AS "product_name_lenght", 
    TRY_CAST(NULLIF(TRIM(r."product_description_lenght"), '') AS INTEGER) AS "product_description_lenght", 
    TRY_CAST(NULLIF(TRIM(r."product_photos_qty"), '') AS INTEGER) AS "product_photos_qty", 
    TRY_CAST(NULLIF(TRIM(r."product_weight_g"), '') AS DOUBLE) AS "product_weight_g", 
    TRY_CAST(NULLIF(TRIM(r."product_length_cm"), '') AS DOUBLE) AS "product_length_cm", 
    TRY_CAST(NULLIF(TRIM(r."product_height_cm"), '') AS DOUBLE) AS "product_height_cm", 
    TRY_CAST(NULLIF(TRIM(r."product_width_cm"), '') AS DOUBLE) AS "product_width_cm", 
    r."product_name_lenght" AS "_src_product_name_lenght",
    r."product_description_lenght" AS "_src_product_description_lenght",
    r."product_photos_qty" AS "_src_product_photos_qty",
    r."product_weight_g" AS "_src_product_weight_g",
    r."product_length_cm" AS "_src_product_length_cm",
    r."product_height_cm" AS "_src_product_height_cm",
    r."product_width_cm" AS "_src_product_width_cm"
FROM raw."products" AS r;
