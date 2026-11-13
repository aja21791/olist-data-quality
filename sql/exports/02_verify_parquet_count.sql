-- Verificar que la exportación conserve el número de filas.
SELECT COUNT(*) FROM read_parquet({file});
