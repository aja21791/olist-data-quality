-- Fase 3: prefijo postal = 5 dígitos. NO corresponde al código postal completo.
-- El mismo prefijo puede repetirse, especialmente en geolocation.
SELECT COUNT(*) AS total_rows,
       SUM(CASE WHEN NULLIF(TRIM({column}),'') IS NULL THEN 1 ELSE 0 END) AS missing_prefix,
       SUM(CASE WHEN NULLIF(TRIM({column}),'') IS NOT NULL
                     AND NOT REGEXP_MATCHES(TRIM({column}), '^[0-9]{{5}}$')
                THEN 1 ELSE 0 END) AS invalid_format,
       COUNT(DISTINCT NULLIF(TRIM({column}),'')) AS distinct_prefixes
FROM raw.{table};
