SELECT service, COUNT(*) AS n_sales, SUM(amount_atomic) AS usdc_atomic FROM sales WHERE own_wallet = 0 GROUP BY service ORDER BY n_sales DESC, service
