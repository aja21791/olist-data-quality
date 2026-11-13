-- Actividad 15: unicidad y completitud de una clave candidata en CLEAN.
SELECT (SELECT COUNT(*) FROM clean.{table} WHERE {required_condition}) AS missing_critical,
       (SELECT COALESCE(SUM(n - 1),0) FROM (
         SELECT COUNT(*) AS n FROM clean.{table} GROUP BY {key_list}
       ) AS duplicate_groups) AS duplicate_critical;
