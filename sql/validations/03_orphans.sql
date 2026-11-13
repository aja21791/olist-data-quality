-- Fase 5 / R09: NOT EXISTS no multiplica filas hijas por joins 1:N.
SELECT COUNT(*) AS orphan_rows
FROM clean.{child_table} AS c
WHERE c.{child_col} IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM clean.{parent_table} AS p
      WHERE p.{parent_col} = c.{child_col}
  );
