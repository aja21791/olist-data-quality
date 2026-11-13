-- Actividad 09: referencias inexistentes, no borrar automáticamente hijos.
SELECT COUNT(*) AS orphan_rows
FROM clean.{child_table} AS c
WHERE c.{child_column} IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM clean.{parent_table} AS p
    WHERE p.{parent_column} = c.{child_column}
  );
