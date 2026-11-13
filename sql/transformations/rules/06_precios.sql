-- Actividad 06: valores monetarios negativos en CLEAN (deben ser cero).
SELECT COUNT(*) AS negative_money_rows FROM clean.{table} WHERE {column} < 0;
