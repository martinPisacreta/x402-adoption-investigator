SELECT own_wallet, COUNT(*) AS n_sales, COUNT(DISTINCT payer) AS n_payers, SUM(amount_atomic) AS usdc_atomic FROM sales GROUP BY own_wallet ORDER BY own_wallet
