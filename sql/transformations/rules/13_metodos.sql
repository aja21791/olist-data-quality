-- Actividad 13: métodos fuera de dominio conocido, conservar e investigar.
SELECT COUNT(*) AS unknown_payment_method_rows
FROM clean.order_payments
WHERE payment_type IS NULL OR payment_type NOT IN
 ('credit_card','boleto','voucher','debit_card','not_defined');
