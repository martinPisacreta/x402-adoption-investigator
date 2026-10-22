SELECT SUM(CASE WHEN calls_30d > 0 THEN 1 ELSE 0 END) AS n_active, COUNT(*) AS n_resources FROM catalog_resources
