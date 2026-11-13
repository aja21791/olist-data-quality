-- Actividad 07: IQR, solo diagnóstico. Jamás elimina automáticamente extremos.
WITH quartiles AS (
 SELECT QUANTILE_CONT({column}, 0.25) AS q1, QUANTILE_CONT({column}, 0.75) AS q3
 FROM clean.{table} WHERE {column} IS NOT NULL
)
SELECT q1, q3, q3-q1 AS iqr,
 (SELECT COUNT(*) FROM clean.{table} t
  WHERE t.{column} < q1 - 1.5*(q3-q1) OR t.{column} > q3 + 1.5*(q3-q1)) AS outlier_rows
FROM quartiles;
