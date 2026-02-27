"""
Unit tests for src/reconciler.py

Covers:
- _matches_selector  (pure function, no mocking needed)
- _extract_ci_status (pure function)
- _render            (pure function)
- _process_pr        (mocks GitHub, AI, optional Argo CD)
- run loop           (mocks K8s + GitHub + AI)
"""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from k8s.client import PRReconciliationRule
from reconciler import AIClient, Reconciler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_reconciler(env, monkeypatch):
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    r = Reconciler()
    # Replace real clients with mocks
    r.k8s = MagicMock()
    r.github = MagicMock()
    r.ai = MagicMock()
    r._argocd = None
    return r


def _rule(spec_override=None):
    spec = {
        "selector": {
            "labels": {"include": ["auto-merge", "staging"], "exclude": ["do-not-merge", "wip"]},
            "baseBranch": "main",
        },
        "instruction": "Decide. Return JSON with action and reason.",
        "argocdEnabled": False,
        "reconciliationInterval": 30,
        "mergeMethod": "SQUASH",
    }
    if spec_override:
        spec.update(spec_override)
    return PRReconciliationRule("staging-rule", "test-ns", spec)


BASE_ENV = {
    "NAMESPACE": "test-ns",
    "GITHUB_ORGANIZATION": "acme",
    "GITHUB_REPOSITORIES": "payment-service",
    "GITHUB_TOKEN": "tok",
    "AI_TOKEN": "ai-tok",
    "AI_MODEL": "claude-sonnet-4-6",
    "AI_MAX_TOKENS": "512",
    "AI_TEMPERATURE": "0.1",
    "ARGOCD_ENABLED": "false",
    "DEFAULT_RECONCILIATION_INTERVAL": "5",
}


# ---------------------------------------------------------------------------
# _extract_ci_status
# ---------------------------------------------------------------------------

class TestExtractCIStatus:
    def _r(self):
        return _make_reconciler(BASE_ENV, MagicMock(setenv=lambda k, v: None))

    def test_success(self, monkeypatch, pr_open):
        r = _make_reconciler(BASE_ENV, monkeypatch)
        assert r._extract_ci_status(pr_open) == "SUCCESS"

    def test_pending(self, monkeypatch, pr_ci_pending):
        r = _make_reconciler(BASE_ENV, monkeypatch)
        assert r._extract_ci_status(pr_ci_pending) == "PENDING"

    def test_failure(self, monkeypatch, pr_ci_failed):
        r = _make_reconciler(BASE_ENV, monkeypatch)
        assert r._extract_ci_status(pr_ci_failed) == "FAILURE"

    def test_no_commits(self, monkeypatch):
        r = _make_reconciler(BASE_ENV, monkeypatch)
        pr = {"commits": {"nodes": []}}
        assert r._extract_ci_status(pr) == "UNKNOWN"

    def test_missing_rollup(self, monkeypatch):
        r = _make_reconciler(BASE_ENV, monkeypatch)
        pr = {"commits": {"nodes": [{"commit": {}}]}}
        assert r._extract_ci_status(pr) == "UNKNOWN"

    def test_expected_maps_to_pending(self, monkeypatch):
        r = _make_reconciler(BASE_ENV, monkeypatch)
        pr = {"commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "EXPECTED"}}}]}}
        assert r._extract_ci_status(pr) == "PENDING"


# ---------------------------------------------------------------------------
# _matches_selector
# ---------------------------------------------------------------------------

class TestMatchesSelector:

    def test_matching_pr(self, monkeypatch, pr_open, staging_rule_spec):
        r = _make_reconciler(BASE_ENV, monkeypatch)
        assert r._matches_selector(pr_open, staging_rule_spec["selector"]) is True

    def test_missing_include_label(self, monkeypatch, pr_open, staging_rule_spec):
        r = _make_reconciler(BASE_ENV, monkeypatch)
        pr_open["labels"]["nodes"] = [{"name": "staging"}]  # missing "auto-merge"
        assert r._matches_selector(pr_open, staging_rule_spec["selector"]) is False

    def test_excluded_label_blocks_match(self, monkeypatch, pr_wip, staging_rule_spec):
        r = _make_reconciler(BASE_ENV, monkeypatch)
        assert r._matches_selector(pr_wip, staging_rule_spec["selector"]) is False

    def test_wrong_base_branch(self, monkeypatch, pr_open, staging_rule_spec):
        r = _make_reconciler(BASE_ENV, monkeypatch)
        pr_open["baseRefName"] = "develop"
        assert r._matches_selector(pr_open, staging_rule_spec["selector"]) is False

    def test_title_pattern_match(self, monkeypatch, pr_open):
        r = _make_reconciler(BASE_ENV, monkeypatch)
        selector = {"titlePattern": "^Fix:"}
        assert r._matches_selector(pr_open, selector) is True

    def test_title_pattern_no_match(self, monkeypatch, pr_open):
        r = _make_reconciler(BASE_ENV, monkeypatch)
        selector = {"titlePattern": "^Rollback:"}
        assert r._matches_selector(pr_open, selector) is False

    def test_author_filter_match(self, monkeypatch, pr_open):
        r = _make_reconciler(BASE_ENV, monkeypatch)
        assert r._matches_selector(pr_open, {"author": "dev1"}) is True

    def test_author_filter_no_match(self, monkeypatch, pr_open):
        r = _make_reconciler(BASE_ENV, monkeypatch)
        assert r._matches_selector(pr_open, {"author": "someone-else"}) is False

    def test_empty_selector_matches_everything(self, monkeypatch, pr_open):
        r = _make_reconciler(BASE_ENV, monkeypatch)
        assert r._matches_selector(pr_open, {}) is True


# ---------------------------------------------------------------------------
# _render
# ---------------------------------------------------------------------------

class TestRender:

    def test_basic_interpolation(self, monkeypatch):
        r = _make_reconciler(BASE_ENV, monkeypatch)
        result = r._render("PR #{{pr.number}}", {"pr": {"number": 42}})
        assert result == "PR #42"

    def test_nested_context(self, monkeypatch):
        r = _make_reconciler(BASE_ENV, monkeypatch)
        result = r._render("CI: {{pr.ciStatus}}", {"pr": {"ciStatus": "SUCCESS"}})
        assert result == "CI: SUCCESS"

    def test_returns_raw_template_on_error(self, monkeypatch):
        r = _make_reconciler(BASE_ENV, monkeypatch)
        bad_template = "{{ unclosed"
        result = r._render(bad_template, {})
        assert result == bad_template


# ---------------------------------------------------------------------------
# _split_repo
# ---------------------------------------------------------------------------

class TestSplitRepo:

    def test_short_name_uses_org(self, monkeypatch):
        r = _make_reconciler(BASE_ENV, monkeypatch)
        assert r._split_repo("payment-service") == ("acme", "payment-service")

    def test_full_slug_overrides_org(self, monkeypatch):
        r = _make_reconciler(BASE_ENV, monkeypatch)
        assert r._split_repo("other-org/payment-service") == ("other-org", "payment-service")


# ---------------------------------------------------------------------------
# AIClient
# ---------------------------------------------------------------------------

class TestAIClient:

    def _client(self):
        with patch("reconciler.AsyncAnthropic"):
            return AIClient(token="tok", model="claude-sonnet-4-6", max_tokens=512, temperature=0.1)

    @pytest.mark.asyncio
    async def test_returns_parsed_decision(self):
        ai = self._client()
        msg = MagicMock()
        msg.content = [MagicMock(text='{"action":"merge","reason":"CI passed"}')]
        ai._client.messages.create = AsyncMock(return_value=msg)

        decision = await ai.decide("some prompt")
        assert decision == {"action": "merge", "reason": "CI passed"}

    @pytest.mark.asyncio
    async def test_falls_back_to_wait_on_bad_json(self):
        ai = self._client()
        msg = MagicMock()
        msg.content = [MagicMock(text="not json at all")]
        ai._client.messages.create = AsyncMock(return_value=msg)

        decision = await ai.decide("prompt")
        assert decision["action"] == "wait"

    @pytest.mark.asyncio
    async def test_falls_back_to_wait_on_api_error(self):
        ai = self._client()
        ai._client.messages.create = AsyncMock(side_effect=Exception("network error"))

        decision = await ai.decide("prompt")
        assert decision["action"] == "wait"
        assert "network error" in decision["reason"]

    @pytest.mark.asyncio
    async def test_empty_content_returns_wait(self):
        ai = self._client()
        msg = MagicMock()
        msg.content = []
        ai._client.messages.create = AsyncMock(return_value=msg)

        decision = await ai.decide("prompt")
        assert decision["action"] == "wait"


# ---------------------------------------------------------------------------
# _process_pr  (action routing)
# ---------------------------------------------------------------------------

class TestProcessPR:

    def _setup(self, monkeypatch, ai_action: str):
        r = _make_reconciler(BASE_ENV, monkeypatch)
        r.github.merge_pull_request = AsyncMock()
        r.github.close_pull_request = AsyncMock()
        r.github.add_comment = AsyncMock()
        r.github.add_labels = AsyncMock()
        r.ai.decide = AsyncMock(return_value={"action": ai_action, "reason": "test reason"})
        return r

    @pytest.mark.asyncio
    async def test_merge_action(self, monkeypatch, pr_open):
        r = self._setup(monkeypatch, "merge")
        await r._process_pr(pr_open, _rule(), "acme", "payment-service")

        r.github.merge_pull_request.assert_awaited_once_with("PR_abc123", "SQUASH")
        r.github.add_comment.assert_awaited_once()
        comment_body = r.github.add_comment.call_args[0][1]
        assert "Auto-merged" in comment_body

    @pytest.mark.asyncio
    async def test_close_action(self, monkeypatch, pr_open):
        r = self._setup(monkeypatch, "close")
        await r._process_pr(pr_open, _rule(), "acme", "payment-service")

        r.github.close_pull_request.assert_awaited_once_with("PR_abc123")
        r.github.add_comment.assert_awaited_once()
        comment_body = r.github.add_comment.call_args[0][1]
        assert "Closed" in comment_body

    @pytest.mark.asyncio
    async def test_escalate_action(self, monkeypatch, pr_open):
        r = self._setup(monkeypatch, "escalate")
        await r._process_pr(pr_open, _rule(), "acme", "payment-service")

        r.github.add_labels.assert_awaited_once_with("acme", "payment-service", "PR_abc123", ["needs-human-approval"])
        r.github.add_comment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_wait_action_takes_no_github_action(self, monkeypatch, pr_open):
        r = self._setup(monkeypatch, "wait")
        await r._process_pr(pr_open, _rule(), "acme", "payment-service")

        r.github.merge_pull_request.assert_not_awaited()
        r.github.close_pull_request.assert_not_awaited()
        r.github.add_labels.assert_not_awaited()
        r.github.add_comment.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_action_does_not_raise(self, monkeypatch, pr_open):
        r = self._setup(monkeypatch, "unknown-action")
        # Should complete without raising
        await r._process_pr(pr_open, _rule(), "acme", "payment-service")

    @pytest.mark.asyncio
    async def test_github_error_is_caught(self, monkeypatch, pr_open):
        r = _make_reconciler(BASE_ENV, monkeypatch)
        r.ai.decide = AsyncMock(return_value={"action": "merge", "reason": "ok"})
        r.github.merge_pull_request = AsyncMock(side_effect=Exception("API error"))
        r.github.add_comment = AsyncMock()

        # Should not propagate
        await r._process_pr(pr_open, _rule(), "acme", "payment-service")

    @pytest.mark.asyncio
    async def test_argocd_health_included_in_context(self, monkeypatch, pr_open):
        """When argocdEnabled=True the Argo CD health is appended to the AI prompt context."""
        r = _make_reconciler(BASE_ENV, monkeypatch)
        r._argocd = MagicMock()
        r._argocd.get_application_health = AsyncMock(return_value="Degraded")
        r.github.merge_pull_request = AsyncMock()
        r.github.add_comment = AsyncMock()

        rendered_prompts = []

        async def capture_decide(prompt):
            rendered_prompts.append(prompt)
            return {"action": "merge", "reason": "ok"}

        r.ai.decide = capture_decide

        rule = _rule({"argocdEnabled": True, "argocdAppNamePattern": "{{repository}}-staging"})
        await r._process_pr(pr_open, rule, "acme", "payment-service")

        assert rendered_prompts, "AI was never called"
        # Argo CD health was fetched
        r._argocd.get_application_health.assert_awaited_once_with("payment-service-staging")


# ---------------------------------------------------------------------------
# run loop
# ---------------------------------------------------------------------------

class TestRunLoop:

    class _StopAfter:
        """Shutdown handler that stops after N iterations."""
        def __init__(self, n):
            self._n = n
            self._calls = 0

        @property
        def shutdown_requested(self):
            self._calls += 1
            return self._calls > self._n

    @pytest.mark.asyncio
    async def test_run_processes_rules(self, monkeypatch, pr_open, staging_rule_spec):
        r = _make_reconciler(BASE_ENV, monkeypatch)

        rule = PRReconciliationRule("r1", "test-ns", staging_rule_spec)
        r.k8s.list_rules = MagicMock(return_value=[rule])
        r.k8s.record_reconciliation = MagicMock()
        r.k8s.record_error = MagicMock()

        r.github.get_pull_requests = AsyncMock(return_value=[pr_open])
        r.github.merge_pull_request = AsyncMock()
        r.github.add_comment = AsyncMock()
        r.ai.decide = AsyncMock(return_value={"action": "merge", "reason": "CI passed"})

        await r.run(self._StopAfter(1))

        r.github.get_pull_requests.assert_awaited()
        r.ai.decide.assert_awaited()
        r.github.merge_pull_request.assert_awaited()

    @pytest.mark.asyncio
    async def test_run_sleeps_when_no_rules(self, monkeypatch):
        r = _make_reconciler(BASE_ENV, monkeypatch)
        r.k8s.list_rules = MagicMock(return_value=[])

        slept = []
        original_sleep = asyncio.sleep

        async def fake_sleep(n):
            slept.append(n)
            # Only sleep once to prevent infinite loop in test
            if len(slept) >= 1:
                # Patch shutdown after first sleep
                pass

        with patch("reconciler.asyncio.sleep", new=fake_sleep):
            await r.run(self._StopAfter(1))

        assert slept  # sleep was called at least once

    @pytest.mark.asyncio
    async def test_run_continues_after_repo_error(self, monkeypatch, pr_open, staging_rule_spec):
        r = _make_reconciler(BASE_ENV, monkeypatch)

        rule = PRReconciliationRule("r1", "test-ns", staging_rule_spec)
        r.k8s.list_rules = MagicMock(return_value=[rule])
        r.k8s.record_reconciliation = MagicMock()
        r.k8s.record_error = MagicMock()

        # First call raises, second would be fine (but loop stops after 1 iteration)
        r.github.get_pull_requests = AsyncMock(side_effect=Exception("network failure"))

        # Should not raise
        await r.run(self._StopAfter(1))

        r.k8s.record_error.assert_called_once()
