-- Cada relación obligatoria debe enlazar con una clave padre real.
-- Un valor ausente en la FK también cuenta como relación inválida.
SELECT COUNT(*) AS population,
       COUNT(*) FILTER (
          WHERE {missing_child}
             OR NOT EXISTS (
               SELECT 1 FROM {layer}.{parent} p
               WHERE p.{parent_key} = c.{child_key}
             )
       ) AS orphan_or_missing
FROM {layer}.{child} c;
