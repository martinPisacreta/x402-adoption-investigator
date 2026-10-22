SELECT predicate, value_atomic FROM chain_facts WHERE wallet = LOWER(:wallet) ORDER BY predicate
