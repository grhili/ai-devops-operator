"""
Prometheus metrics for the AI operator.

All counters and gauges are module-level singletons so any module can import
and increment them without passing instances around.
"""

from prometheus_client import Counter, Gauge, start_http_server

import structlog

logger = structlog.get_logger()

# ------------------------------------------------------------------
# Metrics
# ------------------------------------------------------------------

RULES_ACTIVE = Gauge(
    "ai_operator_rules_active",
    "Number of PRReconciliationRules currently loaded",
)

PRS_PROCESSED = Counter(
    "ai_operator_prs_processed_total",
    "Total number of PRs processed across all rules and repos",
)

ACTIONS = Counter(
    "ai_operator_actions_total",
    "Actions taken on PRs, by action type",
    labelnames=["action"],  # merge | close | escalate | wait | unknown
)

ERRORS = Counter(
    "ai_operator_errors_total",
    "Errors encountered, by component",
    labelnames=["component"],  # github | ai | kubernetes | argocd | reconciler
)

RECONCILIATION_LOOPS = Counter(
    "ai_operator_reconciliation_loops_total",
    "Total number of completed reconciliation loop iterations",
)


# ------------------------------------------------------------------
# Server
# ------------------------------------------------------------------

def start_metrics_server(port: int = 9090) -> None:
    """Start the Prometheus HTTP server on the given port."""
    start_http_server(port)
    logger.info("metrics_server_started", port=port)
