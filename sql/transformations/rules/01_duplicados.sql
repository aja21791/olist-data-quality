-- Actividad 01: excedentes por clave de negocio DESPUÉS de normalizar y tipificar.
-- La Fase 3 ya proporciona el diagnóstico independiente sobre RAW.
SELECT COALESCE(SUM(n-1),0) AS duplicate_excess
FROM (SELECT COUNT(*) AS n FROM _candidate_{temp_table} GROUP BY {key_list}) AS groups;
