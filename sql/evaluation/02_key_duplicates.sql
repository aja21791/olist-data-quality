-- Exceso de filas por clave de negocio, NO registros geográficos repetidos legítimos.
SELECT COALESCE(SUM(n - 1), 0) AS duplicate_excess
FROM (
  SELECT COUNT(*) AS n
  FROM {layer}.{table}
  GROUP BY {key_columns}
  HAVING COUNT(*) > 1
) grouped;
