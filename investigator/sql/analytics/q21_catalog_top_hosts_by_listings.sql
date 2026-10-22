SELECT host, COUNT(*) AS n_resources FROM catalog_resources WHERE host IS NOT NULL GROUP BY host ORDER BY n_resources DESC, host LIMIT 4
