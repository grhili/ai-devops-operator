"""
Unit tests for src/kubernetes/client.py

The official kubernetes Python client is mocked so no cluster is needed.
"""

import pytest
from unittest.mock import MagicMock, patch
from kubernetes.client.rest import ApiException

from k8s.client import KubernetesCRDClient, PRReconciliationRule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client() -> KubernetesCRDClient:
    c = KubernetesCRDClient(namespace="test-ns")
    c._api = MagicMock()  # skip actual K8s config loading
    return c


def _raw_rule(name="staging-rule", spec=None, status=None):
    return {
        "metadata": {"name": name, "namespace": "test-ns"},
        "spec": spec or {
            "selector": {"labels": {"include": ["auto-merge"], "exclude": ["wip"]}},
            "instruction": "merge if CI passed",
            "reconciliationInterval": 30,
            "mergeMethod": "SQUASH",
        },
        "status": status or {},
    }


# ---------------------------------------------------------------------------
# PRReconciliationRule value object
# ---------------------------------------------------------------------------

def test_rule_properties(staging_rule_spec):
    rule = PRReconciliationRule("my-rule", "test-ns", staging_rule_spec)

    assert rule.name == "my-rule"
    assert rule.namespace == "test-ns"
    assert rule.selector == staging_rule_spec["selector"]
    assert rule.instruction == staging_rule_spec["instruction"]
    assert rule.argocd_enabled is False
    assert rule.reconciliation_interval == 30
    assert rule.merge_method == "SQUASH"


def test_rule_defaults_for_missing_fields():
    rule = PRReconciliationRule("r", "ns", {})

    assert rule.selector == {}
    assert rule.instruction == ""
    assert rule.argocd_enabled is False
    assert rule.reconciliation_interval == 30
    assert rule.merge_method == "SQUASH"
    assert rule.argocd_app_name_pattern == "{{repository}}-{{environment}}"


def test_rule_repr():
    rule = PRReconciliationRule("foo", "bar", {})
    assert "foo" in repr(rule)
    assert "bar" in repr(rule)


# ---------------------------------------------------------------------------
# list_rules
# ---------------------------------------------------------------------------

def test_list_rules_returns_parsed_objects():
    c = _make_client()
    c._api.list_namespaced_custom_object.return_value = {"items": [_raw_rule("r1"), _raw_rule("r2")]}

    rules = c.list_rules()

    assert len(rules) == 2
    assert rules[0].name == "r1"
    assert rules[1].name == "r2"


def test_list_rules_returns_empty_on_404():
    c = _make_client()
    c._api.list_namespaced_custom_object.side_effect = ApiException(status=404)

    rules = c.list_rules()

    assert rules == []


def test_list_rules_returns_empty_on_generic_error():
    c = _make_client()
    c._api.list_namespaced_custom_object.side_effect = ApiException(status=500)

    rules = c.list_rules()

    assert rules == []


def test_list_rules_passes_correct_crd_coordinates():
    c = _make_client()
    c._api.list_namespaced_custom_object.return_value = {"items": []}

    c.list_rules()

    c._api.list_namespaced_custom_object.assert_called_once_with(
        group="aioperator.io",
        version="v1alpha1",
        namespace="test-ns",
        plural="prreconciliationrules",
    )


# ---------------------------------------------------------------------------
# update_rule_status
# ---------------------------------------------------------------------------

def test_update_rule_status_merges_with_existing():
    c = _make_client()
    rule = PRReconciliationRule("r", "test-ns", {}, status={"processedPRCount": 5})

    c.update_rule_status(rule, {"lastError": "boom"})

    body = c._api.patch_namespaced_custom_object_status.call_args[1]["body"]
    # existing field preserved, new field added
    assert body["status"]["processedPRCount"] == 5
    assert body["status"]["lastError"] == "boom"


def test_update_rule_status_swallows_api_exception():
    c = _make_client()
    c._api.patch_namespaced_custom_object_status.side_effect = ApiException(status=409)
    rule = PRReconciliationRule("r", "test-ns", {})

    # Should not raise
    c.update_rule_status(rule, {"lastError": "conflict"})


# ---------------------------------------------------------------------------
# record_reconciliation
# ---------------------------------------------------------------------------

def test_record_reconciliation_increments_count():
    c = _make_client()
    rule = PRReconciliationRule("r", "test-ns", {}, status={"processedPRCount": 3})

    c.record_reconciliation(rule, processed=4)

    body = c._api.patch_namespaced_custom_object_status.call_args[1]["body"]
    assert body["status"]["processedPRCount"] == 7  # 3 + 4
    assert "lastReconciliationTime" in body["status"]


def test_record_reconciliation_starts_count_at_zero():
    c = _make_client()
    rule = PRReconciliationRule("r", "test-ns", {})

    c.record_reconciliation(rule, processed=2)

    body = c._api.patch_namespaced_custom_object_status.call_args[1]["body"]
    assert body["status"]["processedPRCount"] == 2


# ---------------------------------------------------------------------------
# record_error
# ---------------------------------------------------------------------------

def test_record_error_writes_message():
    c = _make_client()
    rule = PRReconciliationRule("r", "test-ns", {})

    c.record_error(rule, "something broke")

    body = c._api.patch_namespaced_custom_object_status.call_args[1]["body"]
    assert body["status"]["lastError"] == "something broke"
