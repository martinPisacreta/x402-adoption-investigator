SELECT SUBSTR(settled_at, 1, 10) AS day, COUNT(*) AS n_sales, SUM(amount_atomic) AS usdc_atomic FROM sales WHERE own_wallet = 0 GROUP BY SUBSTR(settled_at, 1, 10) ORDER BY day
