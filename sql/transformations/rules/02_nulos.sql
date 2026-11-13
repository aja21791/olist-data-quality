-- Actividad 02: ausentes en campos obligatorios, evaluados sin imputación.
SELECT COUNT(*) FILTER (WHERE _missing_required > 0) AS missing_required_rows
FROM {classified_table};
