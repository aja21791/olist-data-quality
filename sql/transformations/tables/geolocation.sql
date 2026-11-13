-- Fase 4 | geolocation: tipificar y normalizar RAW sin alterar originales.
-- Ejecuta Python en CREATE TEMP TABLE _candidate_<tabla> AS <SELECT>;
-- _src_* permite auditar cada fallo de conversion sin perder el valor original.
SELECT
    to_json(r) AS _raw_json,
    sha256(to_json(r)) AS _raw_hash,
    CASE WHEN regexp_full_match(NULLIF(TRIM(r."geolocation_zip_code_prefix"), ''), '[0-9]{1,5}') THEN LPAD(NULLIF(TRIM(r."geolocation_zip_code_prefix"), ''), 5, '0') ELSE NULLIF(TRIM(r."geolocation_zip_code_prefix"), '') END AS "geolocation_zip_code_prefix", 
    TRY_CAST(NULLIF(TRIM(r."geolocation_lat"), '') AS DOUBLE) AS "geolocation_lat", 
    TRY_CAST(NULLIF(TRIM(r."geolocation_lng"), '') AS DOUBLE) AS "geolocation_lng", 
    LOWER(NULLIF(regexp_replace(TRIM(r."geolocation_city"), '[[:space:]]+', ' ', 'g'), '')) AS "geolocation_city", 
    UPPER(NULLIF(regexp_replace(TRIM(r."geolocation_state"), '[[:space:]]+', ' ', 'g'), '')) AS "geolocation_state", 
    r."geolocation_lat" AS "_src_geolocation_lat",
    r."geolocation_lng" AS "_src_geolocation_lng"
FROM raw."geolocation" AS r;
