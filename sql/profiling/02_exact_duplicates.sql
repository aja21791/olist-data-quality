-- Fase 3: diferencia entre filas totales y filas enteramente distintas.
-- Sólo diagnostica, no elimina registros. {table} se cita como identificador.
SELECT
    (SELECT COUNT(*) FROM raw.{table}) AS total_rows,
    (SELECT COUNT(*) FROM (SELECT DISTINCT * FROM raw.{table}) d) AS distinct_rows;
