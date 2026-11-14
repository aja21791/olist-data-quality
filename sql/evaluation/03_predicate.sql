-- Contar incidencias campo/relación/dominio sin alterar RAW o CLEAN.
SELECT COUNT(*) AS population,
       COUNT(*) FILTER (WHERE {condition}) AS violation_count
FROM {layer}.{table};
