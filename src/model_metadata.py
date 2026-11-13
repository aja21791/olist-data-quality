"""Modelo conceptual Olist: candidatos que SIEMPRE se verifican contra CSV reales.

No son PK/FK declaradas por el archivo CSV; el programa valida columnas,
cardinalidad, faltantes y existencia de referencias antes de afirmarlas.
"""
from dataclasses import dataclass
from .config import EXPECTED_CSV_FILES


@dataclass(frozen=True)
class TableSpec:
    name: str
    filename: str
    description: str
    grain: str
    candidate_key: tuple[str, ...] = ()
    key_note: str = ""


TABLE_SPECS = (
    TableSpec("customers", "olist_customers_dataset.csv", "Clientes asociados a los pedidos", "Una fila por customer_id", ("customer_id",), "customer_unique_id agrupa clientes históricos; NO sustituye a customer_id"),
    TableSpec("orders", "olist_orders_dataset.csv", "Pedidos y acontecimientos del pedido", "Una fila por order_id", ("order_id",)),
    TableSpec("order_items", "olist_order_items_dataset.csv", "Líneas o artículos de pedido", "Una fila por (order_id, order_item_id)", ("order_id", "order_item_id"), "Varios artículos por pedido son legítimos"),
    TableSpec("order_payments", "olist_order_payments_dataset.csv", "Registros de pago de cada pedido", "Una fila por (order_id, payment_sequential)", ("order_id", "payment_sequential"), "Varios pagos por pedido son legítimos"),
    TableSpec("order_reviews", "olist_order_reviews_dataset.csv", "Reseñas sobre pedidos", "Una fila por registro de reseña", (), "review_id no se presume único; verificar review_id y (review_id, order_id) como diagnósticos"),
    TableSpec("products", "olist_products_dataset.csv", "Productos", "Una fila por product_id", ("product_id",)),
    TableSpec("sellers", "olist_sellers_dataset.csv", "Vendedores", "Una fila por seller_id", ("seller_id",)),
    TableSpec("geolocation", "olist_geolocation_dataset.csv", "Observaciones geográficas", "Una fila por observación geográfica; prefijo puede repetirse", (), "geolocation_zip_code_prefix NO es único"),
    TableSpec("category_translation", "product_category_name_translation.csv", "Catálogo de traducción de categorías", "Una fila por categoría de origen", ("product_category_name",)),
)

assert {x.filename for x in TABLE_SPECS} == set(EXPECTED_CSV_FILES)
BY_NAME = {x.name: x for x in TABLE_SPECS}

# (hija, columna hija, padre, columna padre, nota)
RELATIONSHIPS = (
    ("orders", "customer_id", "customers", "customer_id", "Pedido -> cliente"),
    ("order_items", "order_id", "orders", "order_id", "Artículo -> pedido"),
    ("order_items", "product_id", "products", "product_id", "Artículo -> producto"),
    ("order_items", "seller_id", "sellers", "seller_id", "Artículo -> vendedor"),
    ("order_payments", "order_id", "orders", "order_id", "Pago -> pedido"),
    ("order_reviews", "order_id", "orders", "order_id", "Reseña -> pedido"),
    ("products", "product_category_name", "category_translation", "product_category_name", "Traducción: cobertura opcional, no FK obligatoria"),
)

# Solo para examinar rangos/dominios de columnas, si existen realmente.
TIMESTAMP_CANDIDATES = {
    "orders": ("order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date", "order_delivered_customer_date", "order_estimated_delivery_date"),
    "order_items": ("shipping_limit_date",),
    "order_reviews": ("review_creation_date", "review_answer_timestamp"),
}
MONETARY_CANDIDATES = {
    "order_items": ("price", "freight_value"),
    "order_payments": ("payment_value",),
}
CATEGORY_CANDIDATES = {
    "orders": ("order_status",),
    "order_payments": ("payment_type",),
}
