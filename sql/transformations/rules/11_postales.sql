-- Actividad 11: prefijos brasileños (exactamente 5 dígitos, no CEP completo).
SELECT COUNT(*) AS invalid_postal_rows
FROM clean.{table}
WHERE {column} IS NOT NULL AND NOT regexp_full_match({column}, '[0-9]{{5}}');
