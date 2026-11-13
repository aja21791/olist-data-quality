-- Fase 2: CONSULTAS DE INSPECCIÓN (sólo SELECT; no crean CLEAN).
-- Ejecutar DESPUÉS de `python -m src.extract` y contrastar nombres reales
-- con `reports/data_dictionary/raw_columns.csv`.

-- Conteos y esquema observado en RAW:
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'raw'
ORDER BY table_name;

SELECT table_name, column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'raw'
ORDER BY table_name, ordinal_position;

-- Un pedido puede tener varias líneas de artículo. NO se debe deduplicar
-- por order_id únicamente.
SELECT order_id, COUNT(*) AS number_of_items
FROM raw.order_items
GROUP BY order_id
HAVING COUNT(*) > 1
ORDER BY number_of_items DESC
LIMIT 20;

-- Un pedido puede tener varios pagos. La clave candidata es compuesta.
SELECT order_id, payment_sequential, COUNT(*) AS duplicates
FROM raw.order_payments
GROUP BY order_id, payment_sequential
HAVING COUNT(*) > 1;

-- customer_unique_id puede aparecer en varios customer_id:
SELECT customer_unique_id, COUNT(DISTINCT customer_id) AS customer_ids
FROM raw.customers
GROUP BY customer_unique_id
HAVING COUNT(DISTINCT customer_id) > 1
ORDER BY customer_ids DESC
LIMIT 20;

-- Geolocation es de observaciones: prefijo NO es clave primaria.
SELECT geolocation_zip_code_prefix, COUNT(*) AS observations
FROM raw.geolocation
GROUP BY geolocation_zip_code_prefix
HAVING COUNT(*) > 1
ORDER BY observations DESC
LIMIT 20;

-- Evitar producto cartesiano en un join: primero agregar ítems y pagos
-- por pedido, si se requiere comparar sus montos. Esto se abordará en Fase 4.
