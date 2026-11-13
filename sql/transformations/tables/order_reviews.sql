-- Fase 4 | order_reviews: tipificar y normalizar RAW sin alterar originales.
-- Ejecuta Python en CREATE TEMP TABLE _candidate_<tabla> AS <SELECT>;
-- _src_* permite auditar cada fallo de conversion sin perder el valor original.
SELECT
    to_json(r) AS _raw_json,
    sha256(to_json(r)) AS _raw_hash,
    NULLIF(r."review_id", '') AS "review_id", 
    NULLIF(r."order_id", '') AS "order_id", 
    TRY_CAST(NULLIF(TRIM(r."review_score"), '') AS INTEGER) AS "review_score", 
    CASE WHEN LOWER(NULLIF(TRIM(r."review_comment_title"), '')) IN ('null','none','nan') THEN NULL ELSE NULLIF(TRIM(r."review_comment_title"), '') END AS "review_comment_title", 
    CASE WHEN LOWER(NULLIF(TRIM(r."review_comment_message"), '')) IN ('null','none','nan') THEN NULL ELSE NULLIF(TRIM(r."review_comment_message"), '') END AS "review_comment_message", 
    TRY_CAST(NULLIF(TRIM(r."review_creation_date"), '') AS TIMESTAMP) AS "review_creation_date", 
    TRY_CAST(NULLIF(TRIM(r."review_answer_timestamp"), '') AS TIMESTAMP) AS "review_answer_timestamp", 
    r."review_score" AS "_src_review_score",
    r."review_creation_date" AS "_src_review_creation_date",
    r."review_answer_timestamp" AS "_src_review_answer_timestamp"
FROM raw."order_reviews" AS r;
