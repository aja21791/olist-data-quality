-- Fase 5 / R01: excedentes de clave de negocio, no grupos duplicados.
SELECT COALESCE(SUM(n - 1), 0)::BIGINT AS duplicates
FROM (
    SELECT {keys}, COUNT(*) AS n
    FROM clean.{table}
    GROUP BY {keys}
    HAVING COUNT(*) > 1
) AS duplicate_groups;
