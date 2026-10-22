SELECT payer, COUNT(*) AS n_sales FROM sales WHERE own_wallet = 0 GROUP BY payer HAVING COUNT(*) >= 2 ORDER BY n_sales DESC, payer
