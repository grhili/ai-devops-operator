"""Shared fixtures for all test modules."""

import json
from pathlib import Path
from typing import Any, Dict

import pytest
import sys

# Make src/ importable without installing
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture
def pr_open() -> Dict[str, Any]:
    return {
        "id": "PR_abc123",
        "number": 42,
        "title": "Fix: payment timeout",
        "state": "OPEN",
        "url": "https://github.com/acme/payment-service/pull/42",
        "author": {"login": "dev1"},
        "baseRefName": "main",
        "headRefName": "fix/timeout",
        "mergeable": "MERGEABLE",
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS", "contexts": {"nodes": []}}}}]},
        "reviews": {"nodes": [{"state": "APPROVED", "author": {"login": "lead"}}]},
        "labels": {"nodes": [{"name": "auto-merge"}, {"name": "staging"}]},
    }


@pytest.fixture
def pr_ci_pending() -> Dict[str, Any]:
    return {
        "id": "PR_abc124",
        "number": 43,
        "title": "Feature: new checkout",
        "state": "OPEN",
        "url": "https://github.com/acme/payment-service/pull/43",
        "author": {"login": "dev2"},
        "baseRefName": "main",
        "headRefName": "feature/checkout",
        "mergeable": "MERGEABLE",
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "PENDING", "contexts": {"nodes": []}}}}]},
        "reviews": {"nodes": []},
        "labels": {"nodes": [{"name": "auto-merge"}, {"name": "staging"}]},
    }


@pytest.fixture
def pr_ci_failed() -> Dict[str, Any]:
    return {
        "id": "PR_abc125",
        "number": 44,
        "title": "Refactor: DB schema",
        "state": "OPEN",
        "url": "https://github.com/acme/payment-service/pull/44",
        "author": {"login": "dev3"},
        "baseRefName": "main",
        "headRefName": "refactor/db",
        "mergeable": "MERGEABLE",
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "FAILURE", "contexts": {"nodes": []}}}}]},
        "reviews": {"nodes": []},
        "labels": {"nodes": [{"name": "auto-merge"}, {"name": "staging"}]},
    }


@pytest.fixture
def pr_conflicting() -> Dict[str, Any]:
    return {
        "id": "PR_abc126",
        "number": 45,
        "title": "Bump lodash",
        "state": "OPEN",
        "url": "https://github.com/acme/payment-service/pull/45",
        "author": {"login": "dependabot[bot]"},
        "baseRefName": "main",
        "headRefName": "dependabot/lodash",
        "mergeable": "CONFLICTING",
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS", "contexts": {"nodes": []}}}}]},
        "reviews": {"nodes": []},
        "labels": {"nodes": [{"name": "dependencies"}]},
    }


@pytest.fixture
def pr_wip() -> Dict[str, Any]:
    return {
        "id": "PR_abc127",
        "number": 46,
        "title": "WIP: new feature",
        "state": "OPEN",
        "url": "https://github.com/acme/payment-service/pull/46",
        "author": {"login": "dev4"},
        "baseRefName": "main",
        "headRefName": "wip/feature",
        "mergeable": "MERGEABLE",
        "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS", "contexts": {"nodes": []}}}}]},
        "reviews": {"nodes": []},
        "labels": {"nodes": [{"name": "auto-merge"}, {"name": "staging"}, {"name": "wip"}]},
    }


@pytest.fixture
def staging_rule_spec() -> Dict[str, Any]:
    return {
        "selector": {
            "labels": {"include": ["auto-merge", "staging"], "exclude": ["do-not-merge", "wip"]},
            "baseBranch": "main",
        },
        "instruction": "Decide: merge if CI passed, wait if pending, escalate otherwise. Return JSON.",
        "argocdEnabled": False,
        "reconciliationInterval": 30,
        "mergeMethod": "SQUASH",
    }
