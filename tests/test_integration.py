"""
Integration tests for the AI operator.

These tests validate the full reconciliation loop with mocked external services.
They test the interaction between all components (K8s client, GitHub client,
AI client, Argo CD client) working together.

Run with: pytest tests/test_integration.py -v
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from k8s.client import PRReconciliationRule
from reconciler import Reconciler


class TestFullReconciliationLoop:
    """Test complete reconciliation workflows end-to-end."""

    @pytest.fixture
    def base_env(self, monkeypatch):
        """Set up base environment variables for reconciler."""
        env = {
            "NAMESPACE": "test-ns",
            "GITHUB_ORGANIZATION": "test-org",
            "GITHUB_REPOSITORIES": "test-repo",
            "GITHUB_TOKEN": "ghp_test",
            "AI_TOKEN": "sk-ant-test",
            "AI_MODEL": "claude-sonnet-4-6",
            "AI_MAX_TOKENS": "512",
            "AI_TEMPERATURE": "0.1",
            "ARGOCD_ENABLED": "false",
            "DEFAULT_RECONCILIATION_INTERVAL": "5",
        }
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        return env

    @pytest.fixture
    def rule(self):
        """Create a sample PRReconciliationRule."""
        spec = {
            "selector": {
                "labels": {
                    "include": ["auto-merge", "staging"],
                    "exclude": ["do-not-merge"],
                },
                "baseBranch": "main",
            },
            "instruction": "Auto-merge if CI passed. Return JSON: {action, reason}",
            "reconciliationInterval": 30,
            "mergeMethod": "SQUASH",
            "argocdEnabled": False,
        }
        return PRReconciliationRule("test-rule", "test-ns", spec)

    @pytest.fixture
    def pr_ready_to_merge(self):
        """A PR that matches selector and has CI passing."""
        return {
            "id": "PR_123",
            "number": 42,
            "title": "feat: add new feature",
            "baseRefName": "main",
            "mergeable": "MERGEABLE",
            "author": {"login": "dev1"},
            "labels": {"nodes": [{"name": "auto-merge"}, {"name": "staging"}]},
            "commits": {
                "nodes": [
                    {
                        "commit": {
                            "statusCheckRollup": {"state": "SUCCESS"}
                        }
                    }
                ]
            },
        }

    @pytest.fixture
    def pr_ci_pending(self):
        """A PR with CI still running."""
        return {
            "id": "PR_456",
            "number": 99,
            "title": "fix: bug fix",
            "baseRefName": "main",
            "mergeable": "MERGEABLE",
            "author": {"login": "dev2"},
            "labels": {"nodes": [{"name": "auto-merge"}, {"name": "staging"}]},
            "commits": {
                "nodes": [
                    {
                        "commit": {
                            "statusCheckRollup": {"state": "PENDING"}
                        }
                    }
                ]
            },
        }

    @pytest.fixture
    def pr_ci_failed(self):
        """A PR with CI failures."""
        return {
            "id": "PR_789",
            "number": 88,
            "title": "refactor: cleanup",
            "baseRefName": "main",
            "mergeable": "MERGEABLE",
            "author": {"login": "dev3"},
            "labels": {"nodes": [{"name": "auto-merge"}, {"name": "staging"}]},
            "commits": {
                "nodes": [
                    {
                        "commit": {
                            "statusCheckRollup": {"state": "FAILURE"}
                        }
                    }
                ]
            },
        }

    @pytest.mark.asyncio
    async def test_successful_auto_merge_flow(self, base_env, rule, pr_ready_to_merge):
        """
        Test happy path: PR matches selector, CI passes, AI decides to merge,
        GitHub merge succeeds, status is recorded.
        """
        r = Reconciler()

        # Mock K8s client
        r.k8s = MagicMock()
        r.k8s.list_rules = MagicMock(return_value=[rule])
        r.k8s.record_reconciliation = MagicMock()
        r.k8s.record_error = MagicMock()

        # Mock GitHub client
        r.github = MagicMock()
        r.github.get_pull_requests = AsyncMock(return_value=[pr_ready_to_merge])
        r.github.merge_pull_request = AsyncMock()
        r.github.add_comment = AsyncMock()

        # Mock AI client
        r.ai = MagicMock()
        r.ai.decide = AsyncMock(return_value={"action": "merge", "reason": "CI passed, all checks green"})

        # Run one reconciliation cycle
        await r._reconcile_rule(rule)

        # Verify the flow
        r.github.get_pull_requests.assert_awaited_once()
        r.ai.decide.assert_awaited_once()
        r.github.merge_pull_request.assert_awaited_once_with("PR_123", "SQUASH")
        r.github.add_comment.assert_awaited_once()
        r.k8s.record_reconciliation.assert_called_once()

        # Verify comment includes AI reason
        comment_args = r.github.add_comment.call_args[0]
        assert "CI passed, all checks green" in comment_args[1]

    @pytest.mark.asyncio
    async def test_wait_on_pending_ci(self, base_env, rule, pr_ci_pending):
        """
        Test that pending CI results in 'wait' action and no GitHub mutations.
        """
        r = Reconciler()

        r.k8s = MagicMock()
        r.k8s.list_rules = MagicMock(return_value=[rule])
        r.k8s.record_reconciliation = MagicMock()

        r.github = MagicMock()
        r.github.get_pull_requests = AsyncMock(return_value=[pr_ci_pending])
        r.github.merge_pull_request = AsyncMock()
        r.github.close_pull_request = AsyncMock()
        r.github.add_labels = AsyncMock()
        r.github.add_comment = AsyncMock()

        r.ai = MagicMock()
        r.ai.decide = AsyncMock(return_value={"action": "wait", "reason": "CI still running"})

        await r._reconcile_rule(rule)

        # Verify no actions taken
        r.github.merge_pull_request.assert_not_awaited()
        r.github.close_pull_request.assert_not_awaited()
        r.github.add_labels.assert_not_awaited()
        r.github.add_comment.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_escalate_on_ci_failure(self, base_env, rule, pr_ci_failed):
        """
        Test that CI failures trigger escalation: label added and comment posted.
        """
        r = Reconciler()

        r.k8s = MagicMock()
        r.k8s.list_rules = MagicMock(return_value=[rule])
        r.k8s.record_reconciliation = MagicMock()

        r.github = MagicMock()
        r.github.get_pull_requests = AsyncMock(return_value=[pr_ci_failed])
        r.github.add_labels = AsyncMock()
        r.github.add_comment = AsyncMock()

        r.ai = MagicMock()
        r.ai.decide = AsyncMock(return_value={"action": "escalate", "reason": "CI failed, needs review"})

        await r._reconcile_rule(rule)

        # Verify escalation
        r.github.add_labels.assert_awaited_once()
        labels_args = r.github.add_labels.call_args[0]
        assert "needs-human-approval" in labels_args[3]

        r.github.add_comment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_multiple_prs_processed(self, base_env, rule, pr_ready_to_merge, pr_ci_pending):
        """
        Test that multiple PRs are processed in a single reconciliation cycle.
        """
        r = Reconciler()

        r.k8s = MagicMock()
        r.k8s.list_rules = MagicMock(return_value=[rule])
        r.k8s.record_reconciliation = MagicMock()

        # Return multiple PRs
        r.github = MagicMock()
        r.github.get_pull_requests = AsyncMock(return_value=[pr_ready_to_merge, pr_ci_pending])
        r.github.merge_pull_request = AsyncMock()
        r.github.add_comment = AsyncMock()

        # AI returns different actions for different PRs
        call_count = 0

        async def ai_decide_side_effect(prompt):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"action": "merge", "reason": "PR #42: CI passed"}
            else:
                return {"action": "wait", "reason": "PR #99: CI pending"}

        r.ai = MagicMock()
        r.ai.decide = AsyncMock(side_effect=ai_decide_side_effect)

        await r._reconcile_rule(rule)

        # Verify both PRs were evaluated
        assert r.ai.decide.await_count == 2

        # Verify only first PR was merged
        r.github.merge_pull_request.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_github_error_is_caught_and_logged(self, base_env, rule, pr_ready_to_merge):
        """
        Test that GitHub API errors don't crash reconciliation and are recorded.
        """
        r = Reconciler()

        r.k8s = MagicMock()
        r.k8s.list_rules = MagicMock(return_value=[rule])
        r.k8s.record_reconciliation = MagicMock()
        r.k8s.record_error = MagicMock()

        r.github = MagicMock()
        r.github.get_pull_requests = AsyncMock(side_effect=Exception("GitHub API timeout"))

        r.ai = MagicMock()

        # Should not raise
        await r._reconcile_rule(rule)

        # Verify error was recorded
        r.k8s.record_error.assert_called_once()
        error_msg = r.k8s.record_error.call_args[0][1]
        assert "GitHub API timeout" in error_msg

    @pytest.mark.asyncio
    async def test_ai_error_falls_back_to_wait(self, base_env, rule, pr_ready_to_merge):
        """
        Test that AI errors result in 'wait' action and don't crash reconciliation.
        """
        r = Reconciler()

        r.k8s = MagicMock()
        r.k8s.list_rules = MagicMock(return_value=[rule])
        r.k8s.record_reconciliation = MagicMock()

        r.github = MagicMock()
        r.github.get_pull_requests = AsyncMock(return_value=[pr_ready_to_merge])
        r.github.merge_pull_request = AsyncMock()

        r.ai = MagicMock()
        r.ai.decide = AsyncMock(side_effect=Exception("AI API rate limit"))

        await r._reconcile_rule(rule)

        # Verify no GitHub actions taken
        r.github.merge_pull_request.assert_not_awaited()

        # Reconciliation still completes
        r.k8s.record_reconciliation.assert_called_once()

    @pytest.mark.asyncio
    async def test_selector_filters_prs(self, base_env, rule):
        """
        Test that PRs not matching selector are skipped.
        """
        r = Reconciler()

        # PR missing required label
        pr_no_match = {
            "id": "PR_999",
            "number": 11,
            "title": "no labels",
            "baseRefName": "main",
            "mergeable": "MERGEABLE",
            "author": {"login": "dev4"},
            "labels": {"nodes": []},  # Missing auto-merge label
            "commits": {
                "nodes": [
                    {
                        "commit": {
                            "statusCheckRollup": {"state": "SUCCESS"}
                        }
                    }
                ]
            },
        }

        r.k8s = MagicMock()
        r.k8s.list_rules = MagicMock(return_value=[rule])
        r.k8s.record_reconciliation = MagicMock()

        r.github = MagicMock()
        r.github.get_pull_requests = AsyncMock(return_value=[pr_no_match])

        r.ai = MagicMock()
        r.ai.decide = AsyncMock()

        await r._reconcile_rule(rule)

        # Verify AI was never called (PR filtered out)
        r.ai.decide.assert_not_awaited()


class TestRuleLoop:
    """Test the per-rule async task loop."""

    @pytest.fixture
    def base_env(self, monkeypatch):
        env = {
            "NAMESPACE": "test-ns",
            "GITHUB_ORGANIZATION": "test-org",
            "GITHUB_REPOSITORIES": "test-repo",
            "GITHUB_TOKEN": "ghp_test",
            "AI_TOKEN": "sk-ant-test",
            "AI_MODEL": "claude-sonnet-4-6",
            "AI_MAX_TOKENS": "512",
            "AI_TEMPERATURE": "0.1",
            "ARGOCD_ENABLED": "false",
            "DEFAULT_RECONCILIATION_INTERVAL": "5",
        }
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        return env

    @pytest.mark.asyncio
    async def test_rule_loop_respects_reconciliation_interval(self, base_env):
        """
        Test that _rule_loop sleeps for the rule's reconciliationInterval.
        """
        r = Reconciler()

        spec = {
            "selector": {},
            "instruction": "test",
            "reconciliationInterval": 123,  # Custom interval
            "mergeMethod": "SQUASH",
        }
        rule = PRReconciliationRule("test-rule", "test-ns", spec)

        r.k8s = MagicMock()
        r.k8s.record_reconciliation = MagicMock()

        r.github = MagicMock()
        r.github.get_pull_requests = AsyncMock(return_value=[])

        shutdown = MagicMock()
        shutdown.shutdown_requested = False

        sleep_called_with = []

        async def capture_sleep(seconds):
            sleep_called_with.append(seconds)
            raise asyncio.CancelledError()

        with patch("reconciler.asyncio.sleep", new=capture_sleep):
            with pytest.raises(asyncio.CancelledError):
                await r._rule_loop(rule, shutdown)

        # Verify sleep was called with rule's interval
        assert 123 in sleep_called_with
