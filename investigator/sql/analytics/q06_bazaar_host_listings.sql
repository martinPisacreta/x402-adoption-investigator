SELECT COUNT(*) AS n_listings, COUNT(DISTINCT pay_to) AS n_pay_to, SUM(calls_30d) AS calls_30d_sum, MAX(payers_30d) AS payers_30d_max FROM bazaar_search WHERE host = :host
