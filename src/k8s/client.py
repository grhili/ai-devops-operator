"""
Kubernetes CRD client.

Wraps the official `kubernetes` Python client (the right choice — it is the
maintained, battle-tested library; no need for anything custom here).

Note: this module lives in src/k8s/ (not src/kubernetes/) to avoid shadowing
the installed `kubernetes` package on sys.path.

Handles:
- Loading in-cluster config (pod) or kubeconfig (local dev)
- Listing / status-patching PRReconciliationRule CRDs
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog
from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = structlog.get_logger()

GROUP = "aioperator.io"
VERSION = "v1alpha1"
PLURAL = "prreconciliationrules"


class PRReconciliationRule:
    """Value object representing a PRReconciliationRule CRD instance."""

    def __init__(self, name: str, namespace: str, spec: Dict[str, Any], status: Optional[Dict[str, Any]] = None):
        self.name = name
        self.namespace = namespace
        self.spec = spec
        self.status = status or {}

    # -- spec helpers -------------------------------------------------------

    @property
    def selector(self) -> Dict[str, Any]:
        return self.spec.get("selector", {})

    @property
    def instruction(self) -> str:
        return self.spec.get("instruction", "")

    @property
    def argocd_enabled(self) -> bool:
        return self.spec.get("argocdEnabled", False)

    @property
    def argocd_app_name_pattern(self) -> str:
        return self.spec.get("argocdAppNamePattern", "{{repository}}-{{environment}}")

    @property
    def reconciliation_interval(self) -> int:
        return self.spec.get("reconciliationInterval", 30)

    @property
    def merge_method(self) -> str:
        return self.spec.get("mergeMethod", "SQUASH")

    def __repr__(self) -> str:
        return f"PRReconciliationRule(name={self.name!r}, namespace={self.namespace!r})"


class KubernetesCRDClient:
    """
    Thin wrapper around the official Kubernetes Python client for CRD access.

    The official `kubernetes` library is the correct ready-made choice here:
      pip install kubernetes
    It supports both in-cluster service-account auth and local kubeconfig.
    There is no need for a custom Docker image — use any Python base image.
    """

    def __init__(self, namespace: str):
        self.namespace = namespace
        self._api: Optional[client.CustomObjectsApi] = None

    def initialize(self):
        """Load kubeconfig (falls back to in-cluster config inside a pod)."""
        try:
            config.load_incluster_config()
            logger.info("kubernetes_config_loaded", source="in-cluster")
        except config.ConfigException:
            config.load_kube_config()
            logger.info("kubernetes_config_loaded", source="kubeconfig")

        self._api = client.CustomObjectsApi()

    # ------------------------------------------------------------------
    # Rule management
    # ------------------------------------------------------------------

    def list_rules(self) -> List[PRReconciliationRule]:
        """Return all PRReconciliationRule objects in the configured namespace."""
        try:
            result = self._api.list_namespaced_custom_object(
                group=GROUP, version=VERSION, namespace=self.namespace, plural=PLURAL
            )
            rules = [
                PRReconciliationRule(
                    name=item["metadata"]["name"],
                    namespace=item["metadata"]["namespace"],
                    spec=item.get("spec", {}),
                    status=item.get("status", {}),
                )
                for item in result.get("items", [])
            ]
            logger.info("loaded_rules", count=len(rules), namespace=self.namespace)
            return rules

        except ApiException as exc:
            if exc.status == 404:
                logger.warning("crd_not_installed", group=GROUP, plural=PLURAL)
                return []
            logger.error("list_rules_failed", error=str(exc))
            return []

    def update_rule_status(self, rule: PRReconciliationRule, patch: Dict[str, Any]) -> None:
        """Merge `patch` into the rule's status sub-resource."""
        merged = {**rule.status, **patch}
        try:
            self._api.patch_namespaced_custom_object_status(
                group=GROUP,
                version=VERSION,
                namespace=rule.namespace,
                plural=PLURAL,
                name=rule.name,
                body={"status": merged},
            )
            logger.debug("updated_rule_status", rule=rule.name, patch=patch)
        except ApiException as exc:
            logger.error("update_rule_status_failed", rule=rule.name, error=str(exc))

    def record_reconciliation(self, rule: PRReconciliationRule, processed: int) -> None:
        """Convenience: stamp lastReconciliationTime and increment processedPRCount."""
        self.update_rule_status(rule, {
            "lastReconciliationTime": datetime.now(tz=timezone.utc).isoformat(),
            "processedPRCount": rule.status.get("processedPRCount", 0) + processed,
        })

    def record_error(self, rule: PRReconciliationRule, error: str) -> None:
        """Stamp the last error message onto the rule status."""
        self.update_rule_status(rule, {"lastError": error})
