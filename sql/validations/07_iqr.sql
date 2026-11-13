-- Fase 5 / R07: solo advertencia; no se descartan valores extremos.
WITH q AS (
 SELECT quantile_cont({column}, 0.25) AS q1,
        quantile_cont({column}, 0.75) AS q3
 FROM clean.{table} WHERE {column} IS NOT NULL
)
SELECT COUNT(*) FILTER (
  WHERE t.{column} IS NOT NULL
    AND (t.{column} < (q.q1 - 1.5*(q.q3-q.q1))
      OR t.{column} > (q.q3 + 1.5*(q.q3-q.q1)))
)::BIGINT AS outliers
FROM clean.{table} AS t CROSS JOIN q;
