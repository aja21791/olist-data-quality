-- Actividad 04: conversiones no confiables, fuente original intacta en RAW.
SELECT COUNT(*) FILTER (WHERE _invalid_cast > 0) AS invalid_cast_rows
FROM {classified_table};
