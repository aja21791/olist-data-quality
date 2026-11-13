-- Fase 5 / R02: un nulo en campo obligatorio es un fallo critico.
SELECT COUNT(*) AS missing_rows
FROM clean.{table}
WHERE {column} IS NULL;
