-- Fase 3: intervalos IQR calculados sobre números que sí se pueden convertir.
-- No etiqueta automáticamente un valor atípico como erróneo.
WITH typed AS (
    SELECT TRY_CAST(NULLIF(TRIM({column}), '') AS DOUBLE) AS amount
    FROM raw.{table}
), valid AS (
    SELECT amount FROM typed WHERE amount IS NOT NULL
), quartiles AS (
    SELECT QUANTILE_CONT(amount, 0.25) AS q1,
           QUANTILE_CONT(amount, 0.75) AS q3,
           COUNT(*) AS valid_count,
           MIN(amount) AS minimum,
           MAX(amount) AS maximum,
           AVG(amount) AS average
    FROM valid
)
SELECT q1, q3, q3 - q1 AS iqr,
       q1 - 1.5 * (q3 - q1) AS lower_fence,
       q3 + 1.5 * (q3 - q1) AS upper_fence,
       valid_count, minimum, maximum, average,
       (SELECT COUNT(*) FROM valid, quartiles q
        WHERE amount < q.q1 - 1.5 * (q.q3 - q.q1)
           OR amount > q.q3 + 1.5 * (q.q3 - q.q1)) AS outlier_count
FROM quartiles;
