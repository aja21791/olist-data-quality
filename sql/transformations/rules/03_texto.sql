-- Actividad 03: filas que requieren normalización de texto o espacios.
SELECT COUNT(*) FILTER (WHERE _normalization > 0) AS rows_normalized,
       COUNT(*) FILTER (WHERE _normalization > 0 AND _reason IS NULL) AS rows_normalized_in_clean
FROM {classified_table};
