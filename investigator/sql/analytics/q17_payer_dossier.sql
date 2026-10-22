SELECT COUNT(*) AS n_sales, SUM(amount_atomic) AS usdc_atomic, MIN(settled_at) AS first_at, MAX(settled_at) AS last_at, COUNT(DISTINCT service) AS n_services FROM sales WHERE payer = :wallet
