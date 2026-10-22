SELECT COUNT(*) AS n_resources, COUNT(DISTINCT host) AS n_hosts, COUNT(DISTINCT pay_to) AS n_pay_to, SUM(calls_30d) AS calls_30d_sum FROM catalog_resources
