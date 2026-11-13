-- Fase 5: los predicados proceden exclusivamente del catalogo interno.
SELECT COUNT(*) AS invalid_rows
FROM clean.{table}
WHERE {condition};
