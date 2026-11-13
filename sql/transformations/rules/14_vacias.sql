-- Actividad 14: cadenas vacías o sólo espacios en campos de texto CLEAN.
SELECT COUNT(*) AS blank_rows FROM clean.{table}
WHERE {column} IS NOT NULL AND TRIM({column}) = '';
