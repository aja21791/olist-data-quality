-- Verificar que el esquema de Parquet coincida con el de CLEAN.
DESCRIBE SELECT * FROM read_parquet({file});
