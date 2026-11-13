-- Actividad 08: categorías de productos sin traducción fiable.
SELECT COUNT(*) AS rows_without_category_mapping
FROM clean.products AS p
WHERE p.product_category_name IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM clean.category_translation AS ct
    WHERE ct.product_category_name = p.product_category_name
  );
