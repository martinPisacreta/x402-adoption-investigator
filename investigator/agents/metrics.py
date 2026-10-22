"""Metric registry: metric id -> query id, reducer, unit, required sources, caveat rules, coverage rule
(SPEC.md section 6.5). Owner: B4.

No number lives here (invariant I1): a MetricSpec names WHERE a value comes from (a committed SQL query in
sql/analytics/ read through a SQL extractor, and a pure-Python reducer over the run exports) and HOW it must be
qualified (caveat codes, coverage rule, the fields it sums), never what the value is. `fields` and `sources` are
static per metric so the Critic C3 pitfall gate is deterministic. Conflict explanations and measurement-basis
strings are rule-written here too; the Writer may rephrase them but never changes their substance.

Statement templates avoid bare structural numbers ("top ten", "30-day") so that the Critic's narrative pass B can
map every numeric token of the rendered answer to a claim value.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

CAVEAT_CODES: tuple[str, ...] = (
    "unique_payers_not_dedupable", "search_limit_20", "self_reported_receipts", "third_party_aggregate",
    "payer_from_transfer_log", "snapshot_dated", "basis_mismatch",
)

# Caveat code -> plain-words phrase the Writer uses (the code itself stays in brackets for the Critic).
CAVEAT_PHRASES: dict[str, str] = {
    "unique_payers_not_dedupable": "per-endpoint unique-payer counters cannot be deduplicated across endpoints",
    "search_limit_20": "the Bazaar search is capped at twenty results, so its count is a floor",
    "self_reported_receipts": "receipts are self-reported by the seller",
    "third_party_aggregate": "x402-trust figures are third-party aggregates with their own window and index",
    "payer_from_transfer_log": "payer and seller were read from the Transfer log, not from tx.from",
    "snapshot_dated": "the catalog snapshot is a dated capture and the live total moves by the minute",
    "basis_mismatch": "Bazaar counters and receipts use different windows and bases",
}

MAX_METRICS_PER_RUN = 25
SQL_QUERY_TIMEOUT_S = 30
SQL_TOTAL_TIMEOUT_S = 60
STALE_EVIDENCE_DAYS = 7


@dataclass(frozen=True)
class MetricSpec:
    id: str
    query_id: str                         # primary committed query (must exist in sql/analytics/)
    reducer: str                          # "reducers.<function>"
    unit: str                             # count | atomic_usdc | share | iso8601 | text
    subject_template: str                 # "seller:host:{host}" | "catalog:cdp-bazaar" | "wallet:base:{wallet}"
    statement_template: str               # filled with {value}/{numerator}/{denominator}/{share_pct}/... by the Analyst
    required_sources: tuple[str, ...]     # families that must be ok, else unavailable[]
    fields: tuple[str, ...] = ()          # source fields touched (C3 pitfall gate)
    sources: tuple[str, ...] = ()         # families touched (C3 pitfall gate)
    caveat_rules: tuple[str, ...] = ()    # caveat codes always attached by the Analyst
    coverage_rule: Optional[str] = None   # "catalog_snapshot" | "live_pages" | "search_capped_20" | None
    period: str = "all_time"
    confidence: float = 0.9
    playbooks: tuple[str, ...] = ()
    extra_query_ids: tuple[str, ...] = ()  # other queries the SQL extractor reads (multi-source metrics)
    sums: tuple[str, ...] = ()            # source fields this metric SUMS (Critic C4 rejects any spec that sums
                                          # l30DaysUniquePayers; no registry entry does, tests/test_critic.py proves the rule)
    sql_backed: bool = True               # False: no committed query exposes this value; reducer only (documented)
    required_exports: tuple[str, ...] = ()  # run exports that must exist (e.g. the Verifier's settlements.json)


def _m(**kwargs) -> MetricSpec:
    return MetricSpec(**kwargs)


METRICS: dict[str, MetricSpec] = {spec.id: spec for spec in [
    # ---- seller_profile ------------------------------------------------------------------------------------
    _m(id="sales_total", query_id="q01_sales_by_wallet_class", reducer="reducers.sales_total", unit="count",
       subject_template="seller:host:{host}", statement_template="The receipts of {host} list {value} settled sales in total.",
       required_sources=("receipts",), fields=("ownWallet", "amountAtomic"), sources=("receipts",),
       caveat_rules=("self_reported_receipts",), playbooks=("seller_profile",)),
    _m(id="own_wallet_sales", query_id="q01_sales_by_wallet_class", reducer="reducers.own_wallet_sales", unit="count",
       subject_template="seller:host:{host}", statement_template="{value} of those sales were paid from the seller's own wallets.",
       required_sources=("receipts",), fields=("ownWallet",), sources=("receipts",),
       caveat_rules=("self_reported_receipts",), playbooks=("seller_profile",)),
    _m(id="own_wallets_total", query_id="q01_sales_by_wallet_class", reducer="reducers.own_wallets_total", unit="count",
       subject_template="seller:host:{host}", statement_template="{value} distinct own wallets settled those self-payments.",
       required_sources=("receipts",), fields=("ownWallet", "payer"), sources=("receipts",),
       caveat_rules=("self_reported_receipts",), playbooks=("seller_profile",)),
    _m(id="external_sales_total", query_id="q01_sales_by_wallet_class", reducer="reducers.external_sales_total", unit="count",
       subject_template="seller:host:{host}", statement_template="{value} sales were paid by external wallets.",
       required_sources=("receipts",), fields=("ownWallet",), sources=("receipts",),
       caveat_rules=("self_reported_receipts",), playbooks=("seller_profile",)),
    _m(id="external_usdc_atomic", query_id="q01_sales_by_wallet_class", reducer="reducers.external_usdc_atomic", unit="atomic_usdc",
       subject_template="seller:host:{host}", statement_template="External sales total {value} atomic USDC ({usdc} USDC).",
       required_sources=("receipts",), fields=("ownWallet", "amountAtomic"), sources=("receipts",),
       caveat_rules=("self_reported_receipts",), playbooks=("seller_profile",)),
    _m(id="external_payers_total", query_id="q02_external_payers", reducer="reducers.external_payers_total", unit="count",
       subject_template="seller:host:{host}", statement_template="{value} distinct external payers appear in the receipts.",
       required_sources=("receipts",), fields=("payer", "ownWallet"), sources=("receipts",),
       caveat_rules=("self_reported_receipts",), playbooks=("seller_profile",)),
    _m(id="self_settlement_share", query_id="q01_sales_by_wallet_class", reducer="reducers.self_settlement_share", unit="share",
       subject_template="seller:host:{host}", statement_template="Self-settlement share: {numerator} of {denominator} sales ({share_pct} %).",
       required_sources=("receipts",), fields=("ownWallet",), sources=("receipts",),
       caveat_rules=("self_reported_receipts",), playbooks=("seller_profile",)),
    _m(id="repeat_external_payers", query_id="q03_repeat_external_payers", reducer="reducers.repeat_external_payers", unit="count",
       subject_template="seller:host:{host}", statement_template="{value} external payer(s) bought more than once.",
       required_sources=("receipts",), fields=("payer", "ownWallet"), sources=("receipts",),
       caveat_rules=("self_reported_receipts",), playbooks=("seller_profile",)),
    _m(id="top_payer_share", query_id="q04_top_payer_share", reducer="reducers.top_payer_share", unit="share",
       subject_template="seller:host:{host}", statement_template="The top external payer {payer} made {numerator} of {denominator} external sales ({share_pct} %).",
       required_sources=("receipts",), fields=("payer", "ownWallet"), sources=("receipts",),
       caveat_rules=("self_reported_receipts",), playbooks=("seller_profile",)),
    _m(id="external_window", query_id="q02_external_payers", reducer="reducers.external_window", unit="iso8601",
       subject_template="seller:host:{host}", statement_template="External sales span {value}.",
       required_sources=("receipts",), fields=("at", "ownWallet"), sources=("receipts",),
       caveat_rules=("self_reported_receipts",), playbooks=("seller_profile",)),
    _m(id="external_sales_by_service", query_id="q05_external_sales_by_service", reducer="reducers.external_sales_by_service", unit="text",
       subject_template="seller:host:{host}", statement_template="External sales by service: {value}.",
       required_sources=("receipts",), fields=("service", "ownWallet"), sources=("receipts",),
       caveat_rules=("self_reported_receipts",), playbooks=("seller_profile",)),
    _m(id="external_sales_by_day", query_id="q18_external_sales_by_day", reducer="reducers.external_sales_by_day", unit="text",
       subject_template="seller:host:{host}", statement_template="External sales per day: {value}.",
       required_sources=("receipts",), fields=("at", "ownWallet"), sources=("receipts",),
       caveat_rules=("self_reported_receipts",), playbooks=("seller_profile",)),
    _m(id="external_sales_30d", query_id="q18_external_sales_by_day", reducer="reducers.external_sales_30d", unit="count",
       subject_template="seller:host:{host}", statement_template="External sales settled inside the 30-day window ending {until}: {value}.",
       required_sources=("receipts",), fields=("at", "ownWallet"), sources=("receipts",),
       caveat_rules=("self_reported_receipts", "basis_mismatch"), period="l30d", playbooks=("seller_profile",)),
    _m(id="bazaar_listings_for_host", query_id="q06_bazaar_host_listings", reducer="reducers.bazaar_listings_for_host", unit="count",
       subject_template="seller:host:{host}", statement_template="The CDP Bazaar search returns {value} listings for {host}.",
       required_sources=("cdp_search",), fields=("resource", "payTo"), sources=("cdp_search",),
       caveat_rules=("search_limit_20",), coverage_rule="search_capped_20", playbooks=("seller_profile", "payer_dossier")),
    _m(id="bazaar_payto_for_host", query_id="q06_bazaar_host_listings", reducer="reducers.bazaar_payto_for_host", unit="count",
       subject_template="seller:host:{host}", statement_template="Those listings pay to {value} distinct wallet(s).",
       required_sources=("cdp_search",), fields=("payTo",), sources=("cdp_search",),
       caveat_rules=("search_limit_20",), coverage_rule="search_capped_20", playbooks=("seller_profile",)),
    _m(id="bazaar_calls_30d_for_host", query_id="q06_bazaar_host_listings", reducer="reducers.bazaar_calls_30d_for_host", unit="count",
       subject_template="seller:host:{host}", statement_template="Bazaar 30-day call counters summed over the listings of {host}: {value}.",
       required_sources=("cdp_search",), fields=("l30DaysTotalCalls",), sources=("cdp_search",),
       caveat_rules=("search_limit_20", "basis_mismatch"), coverage_rule="search_capped_20", period="l30d",
       sums=("l30DaysTotalCalls",), playbooks=("seller_profile",)),
    _m(id="bazaar_payto_matches_seller", query_id="q07_bazaar_payto_vs_receipts_seller", reducer="reducers.bazaar_payto_matches_seller", unit="count",
       subject_template="seller:host:{host}", statement_template="{value} Bazaar listings pay to the wallet the receipts name as seller.",
       required_sources=("cdp_search", "receipts"), fields=("payTo", "seller"), sources=("cdp_search", "receipts"),
       caveat_rules=("search_limit_20", "self_reported_receipts"), playbooks=("seller_profile",)),
    _m(id="endpoint_count_for_host", query_id="q12_registry_count_openapi", reducer="reducers.endpoint_count_for_host", unit="text",
       subject_template="seller:host:{host}", statement_template="Registries disagree on the endpoint count for {host}: {value}.",
       required_sources=("store_openapi",), fields=("paths", "paidToolCount", "endpoints", "total"),
       sources=("store_openapi", "cdp_search", "agent402", "vet402", "x402_trust"),
       caveat_rules=("search_limit_20", "third_party_aggregate"),
       extra_query_ids=("q06_bazaar_host_listings", "q13_registry_count_agent402", "q14_registry_count_vet402", "q15_registry_count_trust_search"),
       playbooks=("seller_profile",)),
    _m(id="settlements_verified", query_id="q16_settlements_vs_receipts", reducer="reducers.settlements_verified", unit="count",
       subject_template="seller:host:{host}", statement_template="{value} sampled settlement(s) verified on Base against the receipts.",
       required_sources=("receipts", "base_rpc"), fields=("transaction", "payer", "amountAtomic"), sources=("receipts", "base_rpc"),
       caveat_rules=("payer_from_transfer_log",), playbooks=("seller_profile", "payer_dossier"), required_exports=("settlements",)),
    # ---- catalog_concentration -----------------------------------------------------------------------------
    _m(id="catalog_resources", query_id="q08_catalog_size", reducer="reducers.catalog_resources", unit="count",
       subject_template="catalog:cdp-bazaar", statement_template="The Bazaar catalog snapshot holds {value} unique resources.",
       required_sources=("cdp_catalog_snapshot",), fields=("resource",), sources=("cdp_catalog_snapshot",),
       caveat_rules=("snapshot_dated",), coverage_rule="catalog_snapshot", playbooks=("catalog_concentration",)),
    _m(id="catalog_hosts", query_id="q08_catalog_size", reducer="reducers.catalog_hosts", unit="count",
       subject_template="catalog:cdp-bazaar", statement_template="Those resources are served by {value} distinct hosts.",
       required_sources=("cdp_catalog_snapshot",), fields=("resource",), sources=("cdp_catalog_snapshot",),
       caveat_rules=("snapshot_dated",), coverage_rule="catalog_snapshot", playbooks=("catalog_concentration",)),
    _m(id="catalog_distinct_payto", query_id="q08_catalog_size", reducer="reducers.catalog_distinct_payto", unit="count",
       subject_template="catalog:cdp-bazaar", statement_template="{value} distinct payTo wallets receive the payments.",
       required_sources=("cdp_catalog_snapshot",), fields=("payTo",), sources=("cdp_catalog_snapshot",),
       caveat_rules=("snapshot_dated",), coverage_rule="catalog_snapshot", playbooks=("catalog_concentration",)),
    _m(id="catalog_calls_30d_sum", query_id="q08_catalog_size", reducer="reducers.catalog_calls_30d_sum", unit="count",
       subject_template="catalog:cdp-bazaar", statement_template="The 30-day call counters sum to {value} across the snapshot.",
       required_sources=("cdp_catalog_snapshot",), fields=("l30DaysTotalCalls",), sources=("cdp_catalog_snapshot",),
       caveat_rules=("snapshot_dated",), coverage_rule="catalog_snapshot", period="l30d", sums=("l30DaysTotalCalls",),
       playbooks=("catalog_concentration",)),
    _m(id="catalog_active_share", query_id="q10_catalog_active_share", reducer="reducers.catalog_active_share", unit="share",
       subject_template="catalog:cdp-bazaar", statement_template="{numerator} of {denominator} resources saw at least one call in the 30-day window ({share_pct} %).",
       required_sources=("cdp_catalog_snapshot",), fields=("l30DaysTotalCalls",), sources=("cdp_catalog_snapshot",),
       caveat_rules=("snapshot_dated",), coverage_rule="catalog_snapshot", period="l30d", playbooks=("catalog_concentration",)),
    _m(id="top_host_calls_share", query_id="q09_catalog_top_hosts_by_calls", reducer="reducers.top_host_calls_share", unit="share",
       subject_template="catalog:cdp-bazaar", statement_template="The top host {host} accounts for {numerator} of {denominator} 30-day calls ({share_pct} %).",
       required_sources=("cdp_catalog_snapshot",), fields=("l30DaysTotalCalls",), sources=("cdp_catalog_snapshot",),
       caveat_rules=("snapshot_dated",), coverage_rule="catalog_snapshot", period="l30d", sums=("l30DaysTotalCalls",),
       playbooks=("catalog_concentration",)),
    _m(id="top10_hosts_calls_share", query_id="q09_catalog_top_hosts_by_calls", reducer="reducers.top10_hosts_calls_share", unit="share",
       subject_template="catalog:cdp-bazaar", statement_template="The top ten hosts account for {numerator} of {denominator} 30-day calls ({share_pct} %).",
       required_sources=("cdp_catalog_snapshot",), fields=("l30DaysTotalCalls",), sources=("cdp_catalog_snapshot",),
       caveat_rules=("snapshot_dated",), coverage_rule="catalog_snapshot", period="l30d", sums=("l30DaysTotalCalls",),
       playbooks=("catalog_concentration",)),
    _m(id="top_hosts_by_listings", query_id="q09_catalog_top_hosts_by_calls", reducer="reducers.top_hosts_by_listings", unit="text",
       subject_template="catalog:cdp-bazaar", statement_template="Hosts with the most listings in the snapshot: {value}.",
       required_sources=("cdp_catalog_snapshot",), fields=("resource",), sources=("cdp_catalog_snapshot",),
       caveat_rules=("snapshot_dated",), coverage_rule="catalog_snapshot", playbooks=("catalog_concentration",),
       sql_backed=False),
    _m(id="top_payto_calls_share", query_id="q11_catalog_payto_concentration", reducer="reducers.top_payto_calls_share", unit="share",
       subject_template="catalog:cdp-bazaar", statement_template="The top payTo wallet {pay_to} receives {numerator} of {denominator} 30-day calls ({share_pct} %).",
       required_sources=("cdp_catalog_snapshot",), fields=("l30DaysTotalCalls", "payTo"), sources=("cdp_catalog_snapshot",),
       caveat_rules=("snapshot_dated",), coverage_rule="catalog_snapshot", period="l30d", sums=("l30DaysTotalCalls",),
       playbooks=("catalog_concentration",)),
    _m(id="unique_payers_per_endpoint_max", query_id="q08_catalog_size", reducer="reducers.unique_payers_per_endpoint_max", unit="count",
       subject_template="catalog:cdp-bazaar", statement_template="The largest per-endpoint 30-day unique-payer counter is {value} (not deduplicable across endpoints).",
       required_sources=("cdp_catalog_snapshot",), fields=("l30DaysUniquePayers",), sources=("cdp_catalog_snapshot",),
       caveat_rules=("unique_payers_not_dedupable", "snapshot_dated"), coverage_rule="catalog_snapshot", period="l30d",
       playbooks=("catalog_concentration",), sql_backed=False),
    _m(id="trust_ecosystem_stats", query_id="q19_trust_ecosystem_stats", reducer="reducers.trust_ecosystem_stats", unit="text",
       subject_template="registry:name:x402-trust", statement_template="x402-trust reports {value}.",
       required_sources=("x402_trust",), fields=("endpointsListed", "endpointsActive", "settlements30d"), sources=("x402_trust",),
       caveat_rules=("third_party_aggregate",), period="l30d", playbooks=("catalog_concentration",)),
    # ---- payer_dossier -------------------------------------------------------------------------------------
    _m(id="payer_sales_total", query_id="q17_payer_dossier", reducer="reducers.payer_sales_total", unit="count",
       subject_template="wallet:base:{wallet}", statement_template="The receipts list {value} purchase(s) by {wallet}.",
       required_sources=("receipts",), fields=("payer",), sources=("receipts",),
       caveat_rules=("self_reported_receipts",), playbooks=("payer_dossier",)),
    _m(id="payer_usdc_atomic", query_id="q17_payer_dossier", reducer="reducers.payer_usdc_atomic", unit="atomic_usdc",
       subject_template="wallet:base:{wallet}", statement_template="Those purchases total {value} atomic USDC ({usdc} USDC).",
       required_sources=("receipts",), fields=("payer", "amountAtomic"), sources=("receipts",),
       caveat_rules=("self_reported_receipts",), playbooks=("payer_dossier",)),
    _m(id="payer_window", query_id="q17_payer_dossier", reducer="reducers.payer_window", unit="iso8601",
       subject_template="wallet:base:{wallet}", statement_template="The purchases span {value}.",
       required_sources=("receipts",), fields=("payer", "at"), sources=("receipts",),
       caveat_rules=("self_reported_receipts",), playbooks=("payer_dossier",)),
    _m(id="payer_services", query_id="q17_payer_dossier", reducer="reducers.payer_services", unit="count",
       subject_template="wallet:base:{wallet}", statement_template="They cover {value} distinct service(s).",
       required_sources=("receipts",), fields=("payer", "service"), sources=("receipts",),
       caveat_rules=("self_reported_receipts",), playbooks=("payer_dossier",)),
    _m(id="verified_tx", query_id="q16_settlements_vs_receipts", reducer="reducers.verified_tx", unit="count",
       subject_template="wallet:base:{wallet}", statement_template="{value} settlement(s) of this payer verified on Base.",
       required_sources=("receipts", "base_rpc"), fields=("transaction",), sources=("receipts", "base_rpc"),
       caveat_rules=("payer_from_transfer_log",), playbooks=("payer_dossier",), required_exports=("settlements",)),
    _m(id="usdc_balance_atomic", query_id="q17_payer_dossier", reducer="reducers.usdc_balance_atomic", unit="atomic_usdc",
       subject_template="wallet:base:{wallet}", statement_template="USDC balance at capture: {value} atomic ({usdc} USDC).",
       required_sources=("base_rpc",), fields=("balanceOf",), sources=("base_rpc",),
       caveat_rules=("payer_from_transfer_log",), playbooks=("payer_dossier",), sql_backed=False,
       required_exports=("chain_fact:usdc_balance_atomic",)),
    _m(id="tx_count", query_id="q17_payer_dossier", reducer="reducers.tx_count", unit="count",
       subject_template="wallet:base:{wallet}", statement_template="Transaction count (nonce) at capture: {value}.",
       required_sources=("base_rpc",), fields=("eth_getTransactionCount",), sources=("base_rpc",),
       caveat_rules=(), playbooks=("payer_dossier",), sql_backed=False, required_exports=("chain_fact:tx_count",)),
]}

# Measurement basis per registry source, used when composing Conflicts (rule-written, SPEC.md 6.5).
REGISTRY_BASIS: dict[str, str] = {
    "cdp_search": "CDP Bazaar discovery search, one row per listed resource, capped at twenty results",
    "store_openapi": "paid operations declared by the store's own openapi.json (x-payment-info)",
    "agent402": "tools crawled by Agent402 from the store manifest; paidToolCount is the comparable figure",
    "vet402": "endpoints vet402 has observed for the host with catalog_status listed",
    "x402_trust": "matches reported by the x402-trust keyword search across its own index (listed and delisted)",
}

CONFLICT_EXPLANATIONS: dict[str, str] = {
    "endpoint_count_for_host": (
        "Bazaar search is capped at twenty results and lists one row per resource; the store openapi declares paid "
        "operations; Agent402 counts tools including free ones (paidToolCount is the comparable figure); vet402 lists "
        "endpoints it has observed, including delisted ones; x402-trust search counts matches across its own index."),
    "calls_30d_for_host": (
        "Bazaar l30DaysTotalCalls is a per-endpoint 30-day counter maintained by the facilitator and includes the "
        "seller's own calls; receipts are the seller's own settlement log. Different windows and bases: compare only "
        "with the basis stated."),
    "ecosystem_size": (
        "The Bazaar snapshot is a dated capture of one index deduplicated by resource; the API total moves by the "
        "minute; x402-trust indexes a different and larger set with its own 30-day probe window and counts delisted "
        "endpoints."),
    "backend_disagreement": "The SQL backend result and the pure-Python reducer disagree over the same exports; the claim is kept at confidence 0.5 until the view layer is reconciled.",
    "two_fetches_apart": "Zetaris fetched its own copy of the URL seconds after the Collector; the source moved in between.",
}


def metrics_for_playbook(playbook: str) -> list[str]:
    return [m.id for m in METRICS.values() if playbook in m.playbooks]


def get_metric(metric_id: str) -> MetricSpec:
    return METRICS[metric_id]


def query_ids_for_metrics(metric_ids: list[str]) -> list[str]:
    """Every catalog query the given metrics read (primary + extra), in registry order, deduplicated."""
    seen: list[str] = []
    for metric_id in metric_ids:
        spec = METRICS.get(metric_id)
        if spec is None:
            continue
        for qid in (spec.query_id, *spec.extra_query_ids):
            if spec.sql_backed and qid not in seen:
                seen.append(qid)
    return seen
