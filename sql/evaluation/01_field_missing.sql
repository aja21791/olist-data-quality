-- Fase 8: ausencia REAL (NULL o texto vacío); los campos RAW son VARCHAR.
SELECT COUNT(*) AS population,
       COUNT(*) FILTER (WHERE {missing_predicate}) AS missing_count
FROM {layer}.{table};
