"""
AI Operator — Reconciliation Controller

Orchestrates the reconciliation loop:
  1. Read PRReconciliationRule CRDs  (kubernetes.client)
  2. Fetch matching PRs via GraphQL  (github.client)
  3. Optionally check Argo CD health  (argocd.client)
  4. Render AI prompt and call Claude (anthropic)
  5. Execute the AI decision via GraphQL mutations

Why kopf is not used here
--------------------------
`kopf` (Kubernetes Operator Pythonic Framework) is an excellent alternative
that adds event-driven CRD watching, retry back-off, and leader election out of
the box.  It was intentionally skipped here because the reconciliation pattern
we want is *timer-driven* (poll GitHub every N seconds) rather than
*event-driven* (react to CRD mutations).  kopf supports `@kopf.timer` but
its start-up complexity and required CRD annotations outweigh the benefit for
this use case.  Revisit if HA leader election becomes a requirement.
"""

import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional

import structlog
from anthropic import AsyncAnthropic
from jinja2 import Template

from argocd.client import ArgoCDClient
from github.client import GitHubGraphQLClient
from k8s.client import KubernetesCRDClient, PRReconciliationRule

logger = structlog.get_logger()

_CI_STATE_MAP = {
    "SUCCESS": "SUCCESS",
    "FAILURE": "FAILURE",
    "PENDING": "PENDING",
    "EXPECTED": "PENDING",
    "ERROR": "ERROR",
}


class AIClient:
    """Thin wrapper around the Anthropic async client."""

    def __init__(self, token: str, model: str, max_tokens: int, temperature: float):
        self._client = AsyncAnthropic(api_key=token)
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature

    async def decide(self, prompt: str) -> Dict[str, Any]:
        """
        Call Claude with `prompt` and parse the JSON response.
        Always returns a dict with at least {"action": str, "reason": str}.
        Falls back to {"action": "wait"} on any error so the operator never
        crashes due to an AI failure.
        """
        try:
            msg = await self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                messages=[{"role": "user", "content": prompt}],
            )
            text = msg.content[0].text if msg.content else ""
            if not text:
                return {"action": "wait", "reason": "AI returned empty response"}
            decision = json.loads(text)
            logger.info("ai_decision", action=decision.get("action"), reason=decision.get("reason"))
            return decision
        except json.JSONDecodeError as exc:
            logger.error("ai_response_not_json", error=str(exc))
            return {"action": "wait", "reason": "AI returned non-JSON response"}
        except Exception as exc:
            logger.error("ai_request_failed", error=str(exc), exc_info=True)
            return {"action": "wait", "reason": f"AI request failed: {exc}"}


class Reconciler:
    """
    Main reconciliation controller.

    Reads configuration from environment variables (injected by the Helm chart).
    Delegates all external I/O to the dedicated client modules.
    """

    def __init__(self):
        self.namespace = os.getenv("NAMESPACE", "default")
        self.github_org = os.getenv("GITHUB_ORGANIZATION", "")
        self.github_repos = [r.strip() for r in os.getenv("GITHUB_REPOSITORIES", "").split(",") if r.strip()]
        self.default_interval = int(os.getenv("DEFAULT_RECONCILIATION_INTERVAL", "30"))

        self.k8s = KubernetesCRDClient(namespace=self.namespace)
        self.github = GitHubGraphQLClient(
            endpoint=os.getenv("GITHUB_GRAPHQL_ENDPOINT", "https://api.github.com/graphql"),
            token=os.getenv("GITHUB_TOKEN", ""),
        )
        self.ai = AIClient(
            token=os.getenv("AI_TOKEN", ""),
            model=os.getenv("AI_MODEL", "claude-sonnet-4-6"),
            max_tokens=int(os.getenv("AI_MAX_TOKENS", "1024")),
            temperature=float(os.getenv("AI_TEMPERATURE", "0.2")),
        )

        # Argo CD is optional; only created when enabled
        argocd_enabled = os.getenv("ARGOCD_ENABLED", "false").lower() == "true"
        self._argocd: Optional[ArgoCDClient] = (
            ArgoCDClient(url=os.getenv("ARGOCD_URL", ""), token=os.getenv("ARGOCD_TOKEN", ""))
            if argocd_enabled
            else None
        )

    async def initialize(self) -> None:
        self.k8s.initialize()
        if self._argocd:
            await self._argocd.initialize()
        logger.info("reconciler_initialized", org=self.github_org, repos=self.github_repos)

    async def shutdown(self) -> None:
        if self._argocd:
            await self._argocd.shutdown()
        logger.info("reconciler_stopped")

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run(self, shutdown_handler) -> None:
        logger.info("reconciliation_loop_started")
        while not shutdown_handler.shutdown_requested:
            try:
                rules = self.k8s.list_rules()
                if not rules:
                    logger.warning("no_rules_found", namespace=self.namespace)
                else:
                    await asyncio.gather(
                        *[self._reconcile_rule(rule) for rule in rules],
                        return_exceptions=True,
                    )
            except Exception as exc:
                logger.error("reconciliation_loop_error", error=str(exc), exc_info=True)

            await asyncio.sleep(self.default_interval)

        logger.info("reconciliation_loop_stopped")

    # ------------------------------------------------------------------
    # Per-rule reconciliation
    # ------------------------------------------------------------------

    async def _reconcile_rule(self, rule: PRReconciliationRule) -> None:
        processed = 0
        for repo_entry in self.github_repos:
            owner, repo_name = self._split_repo(repo_entry)
            try:
                prs = await self.github.get_pull_requests(
                    owner,
                    repo_name,
                    labels=rule.selector.get("labels", {}).get("include", []),
                    states=["OPEN"],
                )
                matching = [pr for pr in prs if self._matches_selector(pr, rule.selector)]
                logger.info("prs_matched", rule=rule.name, repo=f"{owner}/{repo_name}",
                            total=len(prs), matching=len(matching))

                for pr in matching:
                    await self._process_pr(pr, rule, owner, repo_name)
                    processed += 1

            except Exception as exc:
                logger.error("reconcile_repo_failed", rule=rule.name,
                             repo=f"{owner}/{repo_name}", error=str(exc), exc_info=True)
                self.k8s.record_error(rule, str(exc))

        self.k8s.record_reconciliation(rule, processed)

    # ------------------------------------------------------------------
    # Per-PR processing
    # ------------------------------------------------------------------

    async def _process_pr(
        self, pr: Dict[str, Any], rule: PRReconciliationRule, owner: str, repo_name: str
    ) -> None:
        pr_number = pr["number"]
        pr_id = pr["id"]
        pr_labels = [l["name"] for l in pr.get("labels", {}).get("nodes", [])]

        context: Dict[str, Any] = {
            "pr": {
                "number": pr_number,
                "title": pr["title"],
                "author": pr.get("author", {}).get("login"),
                "labels": pr_labels,
                "ciStatus": self._extract_ci_status(pr),
                "mergeable": pr.get("mergeable", "UNKNOWN"),
                "reviews": [
                    {"author": r.get("author", {}).get("login"), "state": r.get("state")}
                    for r in pr.get("reviews", {}).get("nodes", [])
                ],
            },
            "repository": repo_name,
        }

        if rule.argocd_enabled and self._argocd:
            environment = next((l for l in pr_labels if l in {"staging", "production", "development"}), "unknown")
            context["environment"] = environment
            app_name = self._render(rule.argocd_app_name_pattern, context)
            context["argocd"] = {"health": await self._argocd.get_application_health(app_name) or "Unknown"}

        prompt = self._render(rule.instruction, context)
        decision = await self.ai.decide(prompt)
        action = decision.get("action", "wait")
        reason = decision.get("reason", "")

        logger.info("pr_decision", pr=pr_number, rule=rule.name, action=action, reason=reason)

        try:
            if action == "merge":
                await self.github.merge_pull_request(pr_id, rule.merge_method)
                await self.github.add_comment(pr_id, f"🤖 Auto-merged: {reason}")

            elif action == "close":
                await self.github.close_pull_request(pr_id)
                await self.github.add_comment(pr_id, f"🤖 Closed: {reason}")

            elif action == "escalate":
                await self.github.add_labels(owner, repo_name, pr_id, ["needs-human-approval"])
                await self.github.add_comment(pr_id, f"⚠️ {reason}")

            elif action == "wait":
                pass  # Re-checked on next loop iteration

            else:
                logger.warning("unknown_action", action=action, pr=pr_number)

        except Exception as exc:
            logger.error("action_failed", pr=pr_number, action=action, error=str(exc), exc_info=True)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _split_repo(self, repo_entry: str):
        """Return (owner, repo_name), defaulting owner to the configured org."""
        if "/" in repo_entry:
            owner, repo_name = repo_entry.split("/", 1)
            return owner, repo_name
        return self.github_org, repo_entry

    def _extract_ci_status(self, pr: Dict[str, Any]) -> str:
        commits = pr.get("commits", {}).get("nodes", [])
        if not commits:
            return "UNKNOWN"
        state = commits[0].get("commit", {}).get("statusCheckRollup", {}).get("state", "UNKNOWN")
        return _CI_STATE_MAP.get(state, "UNKNOWN")

    def _matches_selector(self, pr: Dict[str, Any], selector: Dict[str, Any]) -> bool:
        pr_labels = {l["name"] for l in pr.get("labels", {}).get("nodes", [])}
        include = selector.get("labels", {}).get("include", [])
        exclude = selector.get("labels", {}).get("exclude", [])

        if include and not all(l in pr_labels for l in include):
            return False
        if exclude and any(l in pr_labels for l in exclude):
            return False

        pattern = selector.get("titlePattern", "")
        if pattern and not re.match(pattern, pr.get("title", "")):
            return False

        base_branch = selector.get("baseBranch", "")
        if base_branch and pr.get("baseRefName") != base_branch:
            return False

        author = selector.get("author", "")
        if author and pr.get("author", {}).get("login") != author:
            return False

        return True

    def _render(self, template_str: str, context: Dict[str, Any]) -> str:
        try:
            return Template(template_str).render(**context)
        except Exception as exc:
            logger.error("template_render_failed", error=str(exc))
            return template_str
