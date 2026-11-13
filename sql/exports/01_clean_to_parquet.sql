-- Fase 7. Sólo CLEAN ya aprobado por gate y autorización.
-- {table} y {file} son identificadores/literales previamente validados por Python.
COPY (SELECT * FROM clean.{table}) TO {file}
(FORMAT PARQUET, COMPRESSION ZSTD);
