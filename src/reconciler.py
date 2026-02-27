"""
AI Operator - Reconciliation Controller

This module implements the core reconciliation loop that:
1. Loads PRReconciliationRule CRDs from Kubernetes
2. Fetches matching PRs via GitHub GraphQL MCP
3. Calls AI with templated prompts for decision-making
4. Executes actions (merge, close, comment, label) via GitHub MCP
"""

import asyncio
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import aiohttp
import structlog
from anthropic import AsyncAnthropic
from jinja2 import Template
from kubernetes import client, config
from kubernetes.client.rest import ApiException

logger = structlog.get_logger()


class PRReconciliationRule:
    """Represents a PRReconciliationRule CRD"""

    def __init__(self, name: str, namespace: str, spec: Dict[str, Any], status: Optional[Dict[str, Any]] = None):
        self.name = name
        self.namespace = namespace
        self.spec = spec
        self.status = status or {}

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


class GitHubMCPClient:
    """Client for GitHub MCP GraphQL operations"""

    # GraphQL query to fetch PRs with full context
    GET_PRS_QUERY = """
    query GetPullRequests($owner: String!, $repo: String!, $labels: [String!], $states: [PullRequestState!]) {
      repository(owner: $owner, name: $repo) {
        pullRequests(
          first: 100
          states: $states
          labels: $labels
          orderBy: {field: UPDATED_AT, direction: DESC}
        ) {
          nodes {
            number
            title
            state
            url
            author { login }
            baseRefName
            headRefName
            mergeable
            id
            commits(last: 1) {
              nodes {
                commit {
                  statusCheckRollup {
                    state
                    contexts(first: 100) {
                      nodes {
                        ... on StatusContext {
                          state
                          context
                        }
                        ... on CheckRun {
                          conclusion
                          name
                        }
                      }
                    }
                  }
                }
              }
            }
            reviews(last: 10) {
              nodes {
                state
                author { login }
              }
            }
            labels(first: 20) {
              nodes {
                name
              }
            }
          }
          pageInfo {
            hasNextPage
            endCursor
          }
        }
      }
    }
    """

    MERGE_PR_MUTATION = """
    mutation MergePullRequest($prId: ID!, $mergeMethod: PullRequestMergeMethod!) {
      mergePullRequest(input: {pullRequestId: $prId, mergeMethod: $mergeMethod}) {
        pullRequest {
          number
          merged
          mergedAt
        }
      }
    }
    """

    CLOSE_PR_MUTATION = """
    mutation ClosePullRequest($prId: ID!) {
      closePullRequest(input: {pullRequestId: $prId}) {
        pullRequest {
          number
          closed
          closedAt
        }
      }
    }
    """

    ADD_COMMENT_MUTATION = """
    mutation AddComment($prId: ID!, $body: String!) {
      addComment(input: {subjectId: $prId, body: $body}) {
        commentEdge {
          node {
            id
            body
          }
        }
      }
    }
    """

    ADD_LABELS_MUTATION = """
    mutation AddLabels($labelableId: ID!, $labelIds: [ID!]!) {
      addLabelsToLabelable(input: {labelableId: $labelableId, labelIds: $labelIds}) {
        labelable {
          ... on PullRequest {
            number
            labels(first: 20) {
              nodes {
                name
              }
            }
          }
        }
      }
    }
    """

    GET_LABEL_IDS_QUERY = """
    query GetLabelIds($owner: String!, $repo: String!, $labelNames: [String!]!) {
      repository(owner: $owner, name: $repo) {
        labels(first: 20, query: "") {
          nodes {
            id
            name
          }
        }
      }
    }
    """

    def __init__(self, endpoint: str, token: str):
        self.endpoint = endpoint
        self.token = token
        self.session: Optional[aiohttp.ClientSession] = None

    async def initialize(self):
        """Initialize HTTP session"""
        self.session = aiohttp.ClientSession(
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            }
        )

    async def shutdown(self):
        """Close HTTP session"""
        if self.session:
            await self.session.close()

    async def _graphql_request(self, query: str, variables: Dict[str, Any]) -> Dict[str, Any]:
        """Execute GraphQL request"""
        if not self.session:
            raise RuntimeError("Client not initialized. Call initialize() first.")

        payload = {"query": query, "variables": variables}

        logger.debug("github_graphql_request", query_preview=query[:100], variables=variables)

        async with self.session.post(self.endpoint, json=payload) as response:
            response.raise_for_status()
            result = await response.json()

            if "errors" in result:
                logger.error("github_graphql_error", errors=result["errors"])
                raise RuntimeError(f"GraphQL errors: {result['errors']}")

            return result.get("data", {})

    async def get_pull_requests(
        self, owner: str, repo: str, labels: Optional[List[str]] = None, states: Optional[List[str]] = None
    ) -> List[Dict[str, Any]]:
        """Fetch pull requests matching criteria"""
        variables = {
            "owner": owner,
            "repo": repo,
            "labels": labels or [],
            "states": states or ["OPEN"],
        }

        data = await self._graphql_request(self.GET_PRS_QUERY, variables)
        prs = data.get("repository", {}).get("pullRequests", {}).get("nodes", [])

        logger.info("fetched_pull_requests", count=len(prs), repo=f"{owner}/{repo}", labels=labels)
        return prs

    async def merge_pull_request(self, pr_id: str, merge_method: str = "SQUASH") -> Dict[str, Any]:
        """Merge a pull request"""
        variables = {"prId": pr_id, "mergeMethod": merge_method}
        data = await self._graphql_request(self.MERGE_PR_MUTATION, variables)
        logger.info("merged_pull_request", pr_id=pr_id, merge_method=merge_method)
        return data.get("mergePullRequest", {}).get("pullRequest", {})

    async def close_pull_request(self, pr_id: str) -> Dict[str, Any]:
        """Close a pull request"""
        variables = {"prId": pr_id}
        data = await self._graphql_request(self.CLOSE_PR_MUTATION, variables)
        logger.info("closed_pull_request", pr_id=pr_id)
        return data.get("closePullRequest", {}).get("pullRequest", {})

    async def add_comment(self, pr_id: str, body: str) -> Dict[str, Any]:
        """Add comment to pull request"""
        variables = {"prId": pr_id, "body": body}
        data = await self._graphql_request(self.ADD_COMMENT_MUTATION, variables)
        logger.info("added_comment", pr_id=pr_id, body_preview=body[:50])
        return data.get("addComment", {})

    async def add_labels(self, owner: str, repo: str, pr_id: str, label_names: List[str]) -> Dict[str, Any]:
        """Add labels to pull request"""
        # First, get label IDs
        label_data = await self._graphql_request(
            self.GET_LABEL_IDS_QUERY, {"owner": owner, "repo": repo, "labelNames": label_names}
        )

        all_labels = label_data.get("repository", {}).get("labels", {}).get("nodes", [])
        label_ids = [label["id"] for label in all_labels if label["name"] in label_names]

        if not label_ids:
            logger.warning("no_matching_labels_found", requested=label_names, available=[l["name"] for l in all_labels])
            return {}

        # Add labels
        variables = {"labelableId": pr_id, "labelIds": label_ids}
        data = await self._graphql_request(self.ADD_LABELS_MUTATION, variables)
        logger.info("added_labels", pr_id=pr_id, labels=label_names)
        return data.get("addLabelsToLabelable", {})


class ArgoCDMCPClient:
    """Client for Argo CD MCP health checks"""

    def __init__(self, url: str, token: str, enabled: bool):
        self.url = url
        self.token = token
        self.enabled = enabled
        self.session: Optional[aiohttp.ClientSession] = None

    async def initialize(self):
        """Initialize HTTP session"""
        if self.enabled:
            self.session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                }
            )

    async def shutdown(self):
        """Close HTTP session"""
        if self.session:
            await self.session.close()

    async def get_application_health(self, app_name: str) -> Optional[str]:
        """Get Argo CD application health status"""
        if not self.enabled or not self.session:
            return None

        try:
            url = f"{self.url}/api/v1/applications/{app_name}"
            async with self.session.get(url) as response:
                response.raise_for_status()
                data = await response.json()
                health = data.get("status", {}).get("health", {}).get("status", "Unknown")
                logger.debug("argocd_health_check", app_name=app_name, health=health)
                return health
        except Exception as e:
            logger.error("argocd_health_check_failed", app_name=app_name, error=str(e))
            return None


class AIClient:
    """Client for AI decision-making (Anthropic Claude)"""

    def __init__(self, endpoint: str, token: str, model: str, max_tokens: int, temperature: float):
        self.endpoint = endpoint
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.client = AsyncAnthropic(api_key=token)

    async def make_decision(self, prompt: str) -> Dict[str, Any]:
        """Call AI with prompt and get JSON decision"""
        try:
            logger.debug("ai_request", model=self.model, prompt_preview=prompt[:200])

            message = await self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                messages=[{"role": "user", "content": prompt}],
            )

            # Extract text content
            content = message.content[0].text if message.content else "{}"

            # Parse JSON response
            try:
                decision = json.loads(content)
                logger.info("ai_decision", action=decision.get("action"), reason=decision.get("reason"))
                return decision
            except json.JSONDecodeError as e:
                logger.error("ai_response_not_json", content=content, error=str(e))
                # Return default safe action
                return {"action": "wait", "reason": f"AI response was not valid JSON: {content[:100]}"}

        except Exception as e:
            logger.error("ai_request_failed", error=str(e), exc_info=True)
            # Return safe default
            return {"action": "wait", "reason": f"AI request failed: {str(e)}"}


class Reconciler:
    """Main reconciliation controller"""

    def __init__(self):
        # Configuration from environment
        self.namespace = os.getenv("NAMESPACE", "default")
        self.github_org = os.getenv("GITHUB_ORGANIZATION", "")
        self.github_repos = os.getenv("GITHUB_REPOSITORIES", "").split(",")
        self.default_interval = int(os.getenv("DEFAULT_RECONCILIATION_INTERVAL", "30"))

        # Clients (initialized in initialize())
        self.k8s_client: Optional[client.CustomObjectsApi] = None
        self.github_client: Optional[GitHubMCPClient] = None
        self.argocd_client: Optional[ArgoCDMCPClient] = None
        self.ai_client: Optional[AIClient] = None

    async def initialize(self):
        """Initialize all clients"""
        logger.info("initializing_reconciler")

        # Initialize Kubernetes client
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()

        self.k8s_client = client.CustomObjectsApi()

        # Initialize GitHub MCP client
        github_endpoint = os.getenv("GITHUB_GRAPHQL_ENDPOINT", "https://api.github.com/graphql")
        github_token = os.getenv("GITHUB_TOKEN", "")
        self.github_client = GitHubMCPClient(github_endpoint, github_token)
        await self.github_client.initialize()

        # Initialize Argo CD MCP client
        argocd_enabled = os.getenv("ARGOCD_ENABLED", "false").lower() == "true"
        argocd_url = os.getenv("ARGOCD_URL", "")
        argocd_token = os.getenv("ARGOCD_TOKEN", "")
        self.argocd_client = ArgoCDMCPClient(argocd_url, argocd_token, argocd_enabled)
        await self.argocd_client.initialize()

        # Initialize AI client
        ai_endpoint = os.getenv("AI_ENDPOINT", "https://api.anthropic.com/v1/messages")
        ai_token = os.getenv("AI_TOKEN", "")
        ai_model = os.getenv("AI_MODEL", "claude-3-5-sonnet-20241022")
        ai_max_tokens = int(os.getenv("AI_MAX_TOKENS", "1024"))
        ai_temperature = float(os.getenv("AI_TEMPERATURE", "0.2"))
        self.ai_client = AIClient(ai_endpoint, ai_token, ai_model, ai_max_tokens, ai_temperature)

        logger.info("reconciler_initialized", github_org=self.github_org, repos=self.github_repos)

    async def shutdown(self):
        """Shutdown all clients"""
        logger.info("shutting_down_reconciler")
        if self.github_client:
            await self.github_client.shutdown()
        if self.argocd_client:
            await self.argocd_client.shutdown()

    async def load_rules(self) -> List[PRReconciliationRule]:
        """Load all PRReconciliationRule CRDs from Kubernetes"""
        try:
            result = self.k8s_client.list_namespaced_custom_object(
                group="aioperator.io", version="v1alpha1", namespace=self.namespace, plural="prreconciliationrules"
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

        except ApiException as e:
            if e.status == 404:
                logger.warning("crd_not_found", message="PRReconciliationRule CRD not installed")
                return []
            logger.error("failed_to_load_rules", error=str(e))
            return []

    async def update_rule_status(self, rule: PRReconciliationRule, status_update: Dict[str, Any]):
        """Update PRReconciliationRule status"""
        try:
            # Merge status update
            new_status = {**rule.status, **status_update}

            # Patch the status
            self.k8s_client.patch_namespaced_custom_object_status(
                group="aioperator.io",
                version="v1alpha1",
                namespace=rule.namespace,
                plural="prreconciliationrules",
                name=rule.name,
                body={"status": new_status},
            )

            logger.debug("updated_rule_status", rule=rule.name, status=status_update)

        except ApiException as e:
            logger.error("failed_to_update_rule_status", rule=rule.name, error=str(e))

    def matches_selector(self, pr: Dict[str, Any], selector: Dict[str, Any]) -> bool:
        """Check if PR matches rule selector"""
        # Extract PR labels
        pr_labels = [label["name"] for label in pr.get("labels", {}).get("nodes", [])]

        # Check include labels
        include_labels = selector.get("labels", {}).get("include", [])
        if include_labels and not all(label in pr_labels for label in include_labels):
            return False

        # Check exclude labels
        exclude_labels = selector.get("labels", {}).get("exclude", [])
        if exclude_labels and any(label in pr_labels for label in exclude_labels):
            return False

        # Check title pattern
        title_pattern = selector.get("titlePattern", "")
        if title_pattern and not re.match(title_pattern, pr.get("title", "")):
            return False

        # Check base branch
        base_branch = selector.get("baseBranch", "")
        if base_branch and pr.get("baseRefName") != base_branch:
            return False

        # Check author
        author = selector.get("author", "")
        if author and pr.get("author", {}).get("login") != author:
            return False

        return True

    def extract_ci_status(self, pr: Dict[str, Any]) -> str:
        """Extract CI status from PR"""
        commits = pr.get("commits", {}).get("nodes", [])
        if not commits:
            return "UNKNOWN"

        status_rollup = commits[0].get("commit", {}).get("statusCheckRollup", {})
        state = status_rollup.get("state", "UNKNOWN")

        # Map GitHub states to our simplified states
        state_map = {
            "SUCCESS": "SUCCESS",
            "FAILURE": "FAILURE",
            "PENDING": "PENDING",
            "ERROR": "ERROR",
            "EXPECTED": "PENDING",
        }

        return state_map.get(state, "UNKNOWN")

    def render_template(self, template_str: str, context: Dict[str, Any]) -> str:
        """Render Jinja2 template with context"""
        try:
            template = Template(template_str)
            return template.render(**context)
        except Exception as e:
            logger.error("template_render_failed", error=str(e), template_preview=template_str[:100])
            return template_str

    async def process_pr(self, pr: Dict[str, Any], rule: PRReconciliationRule, repo: str):
        """Process a single PR according to rule"""
        pr_number = pr["number"]
        pr_id = pr["id"]

        logger.info("processing_pr", pr_number=pr_number, rule=rule.name, repo=repo)

        # Build context for AI
        pr_labels = [label["name"] for label in pr.get("labels", {}).get("nodes", [])]
        pr_reviews = [
            {"author": review.get("author", {}).get("login"), "state": review.get("state")}
            for review in pr.get("reviews", {}).get("nodes", [])
        ]

        context = {
            "pr": {
                "number": pr_number,
                "title": pr["title"],
                "author": pr.get("author", {}).get("login"),
                "labels": pr_labels,
                "ciStatus": self.extract_ci_status(pr),
                "mergeable": pr.get("mergeable", "UNKNOWN"),
                "reviews": pr_reviews,
            },
            "repository": repo.split("/")[-1],
        }

        # Add Argo CD health if enabled
        if rule.argocd_enabled:
            # Extract environment from labels or use default
            environment = "unknown"
            for label in pr_labels:
                if label in ["staging", "production", "development"]:
                    environment = label
                    break

            context["environment"] = environment

            # Render app name pattern
            app_name = self.render_template(rule.argocd_app_name_pattern, context)

            # Get health status
            health = await self.argocd_client.get_application_health(app_name)
            context["argocd"] = {"health": health or "Unknown"}

        # Render AI instruction with context
        prompt = self.render_template(rule.instruction, context)

        # Get AI decision
        decision = await self.ai_client.make_decision(prompt)
        action = decision.get("action", "wait")
        reason = decision.get("reason", "No reason provided")

        # Execute action
        try:
            if action == "merge":
                await self.github_client.merge_pull_request(pr_id, rule.merge_method)
                await self.github_client.add_comment(pr_id, f"🤖 Auto-merged: {reason}")
                logger.info("pr_merged", pr_number=pr_number, reason=reason)

            elif action == "close":
                await self.github_client.close_pull_request(pr_id)
                await self.github_client.add_comment(pr_id, f"🤖 Closed: {reason}")
                logger.info("pr_closed", pr_number=pr_number, reason=reason)

            elif action == "escalate":
                owner, repo_name = repo.split("/")
                await self.github_client.add_labels(owner, repo_name, pr_id, ["needs-human-approval"])
                await self.github_client.add_comment(pr_id, f"⚠️ {reason}")
                logger.info("pr_escalated", pr_number=pr_number, reason=reason)

            elif action == "wait":
                logger.debug("pr_waiting", pr_number=pr_number, reason=reason)

            else:
                logger.warning("unknown_action", action=action, pr_number=pr_number)

        except Exception as e:
            logger.error("action_execution_failed", pr_number=pr_number, action=action, error=str(e), exc_info=True)

    async def reconcile_rule(self, rule: PRReconciliationRule):
        """Reconcile all PRs for a single rule"""
        logger.debug("reconciling_rule", rule=rule.name, interval=rule.reconciliation_interval)

        for repo in self.github_repos:
            repo = repo.strip()
            if not repo:
                continue

            try:
                # Extract include labels for query
                include_labels = rule.selector.get("labels", {}).get("include", [])

                # Fetch PRs
                owner = self.github_org
                repo_name = repo
                if "/" in repo:
                    owner, repo_name = repo.split("/", 1)

                prs = await self.github_client.get_pull_requests(owner, repo_name, include_labels, ["OPEN"])

                # Filter PRs by full selector
                matching_prs = [pr for pr in prs if self.matches_selector(pr, rule.selector)]

                logger.info(
                    "rule_prs_matched", rule=rule.name, repo=repo, total=len(prs), matching=len(matching_prs)
                )

                # Process each matching PR
                for pr in matching_prs:
                    await self.process_pr(pr, rule, f"{owner}/{repo_name}")

                # Update rule status
                await self.update_rule_status(
                    rule,
                    {
                        "lastReconciliationTime": datetime.utcnow().isoformat() + "Z",
                        "processedPRCount": rule.status.get("processedPRCount", 0) + len(matching_prs),
                    },
                )

            except Exception as e:
                logger.error("rule_reconciliation_failed", rule=rule.name, repo=repo, error=str(e), exc_info=True)
                await self.update_rule_status(rule, {"lastError": str(e)})

    async def run(self, shutdown_handler):
        """Main reconciliation loop"""
        logger.info("reconciliation_loop_started")

        while not shutdown_handler.shutdown_requested:
            try:
                # Load all rules
                rules = await self.load_rules()

                if not rules:
                    logger.warning("no_rules_found", namespace=self.namespace)
                    await asyncio.sleep(self.default_interval)
                    continue

                # Process each rule concurrently
                tasks = [self.reconcile_rule(rule) for rule in rules]
                await asyncio.gather(*tasks, return_exceptions=True)

                # Sleep for default interval (individual rules may run on different schedules)
                await asyncio.sleep(self.default_interval)

            except Exception as e:
                logger.error("reconciliation_loop_error", error=str(e), exc_info=True)
                await asyncio.sleep(5)  # Backoff on error

        logger.info("reconciliation_loop_stopped")
