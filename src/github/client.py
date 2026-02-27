"""
GitHub GraphQL client using the `gql` library.

Uses GitHub's GraphQL API exclusively for all operations — no REST calls.
The `gql` library handles query validation, transport, and retries,
which is cleaner than raw aiohttp POST calls.
"""

from typing import Any, Dict, List, Optional

import structlog
from gql import Client, gql
from gql.transport.aiohttp import AIOHTTPTransport
from gql.transport.exceptions import TransportQueryError

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

GET_PRS_QUERY = gql("""
    query GetPullRequests(
        $owner: String!,
        $repo: String!,
        $labels: [String!],
        $states: [PullRequestState!]
    ) {
      repository(owner: $owner, name: $repo) {
        pullRequests(
          first: 100
          states: $states
          labels: $labels
          orderBy: {field: UPDATED_AT, direction: DESC}
        ) {
          nodes {
            id
            number
            title
            state
            url
            mergeable
            baseRefName
            headRefName
            author { login }
            commits(last: 1) {
              nodes {
                commit {
                  statusCheckRollup {
                    state
                    contexts(first: 100) {
                      nodes {
                        ... on StatusContext { state context }
                        ... on CheckRun    { conclusion name }
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
              nodes { name }
            }
          }
          pageInfo { hasNextPage endCursor }
        }
      }
    }
""")

GET_LABEL_IDS_QUERY = gql("""
    query GetLabelIds($owner: String!, $repo: String!) {
      repository(owner: $owner, name: $repo) {
        labels(first: 100) {
          nodes { id name }
        }
      }
    }
""")

# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

MERGE_PR_MUTATION = gql("""
    mutation MergePullRequest($prId: ID!, $mergeMethod: PullRequestMergeMethod!) {
      mergePullRequest(input: {pullRequestId: $prId, mergeMethod: $mergeMethod}) {
        pullRequest { number merged mergedAt }
      }
    }
""")

CLOSE_PR_MUTATION = gql("""
    mutation ClosePullRequest($prId: ID!) {
      closePullRequest(input: {pullRequestId: $prId}) {
        pullRequest { number closed closedAt }
      }
    }
""")

ADD_COMMENT_MUTATION = gql("""
    mutation AddComment($prId: ID!, $body: String!) {
      addComment(input: {subjectId: $prId, body: $body}) {
        commentEdge { node { id body } }
      }
    }
""")

ADD_LABELS_MUTATION = gql("""
    mutation AddLabels($labelableId: ID!, $labelIds: [ID!]!) {
      addLabelsToLabelable(input: {labelableId: $labelableId, labelIds: $labelIds}) {
        labelable {
          ... on PullRequest {
            number
            labels(first: 20) { nodes { name } }
          }
        }
      }
    }
""")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class GitHubGraphQLClient:
    """
    Async GitHub GraphQL client backed by `gql`.

    The transport is created once and reused for the lifetime of the operator.
    All operations are GraphQL — no REST fallbacks.
    """

    def __init__(self, endpoint: str, token: str):
        transport = AIOHTTPTransport(
            url=endpoint,
            headers={"Authorization": f"Bearer {token}"},
        )
        # execute_timeout=None lets individual calls set their own timeout
        self._client = Client(transport=transport, fetch_schema_from_transport=False)

    async def get_pull_requests(
        self,
        owner: str,
        repo: str,
        labels: Optional[List[str]] = None,
        states: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Batch-fetch open PRs with CI status, reviews and labels in one query."""
        variables = {
            "owner": owner,
            "repo": repo,
            "labels": labels or [],
            "states": states or ["OPEN"],
        }
        result = await self._execute(GET_PRS_QUERY, variables)
        prs = result.get("repository", {}).get("pullRequests", {}).get("nodes", [])
        logger.info("fetched_pull_requests", count=len(prs), repo=f"{owner}/{repo}", labels=labels)
        return prs

    async def merge_pull_request(self, pr_id: str, merge_method: str = "SQUASH") -> Dict[str, Any]:
        result = await self._execute(MERGE_PR_MUTATION, {"prId": pr_id, "mergeMethod": merge_method})
        logger.info("merged_pull_request", pr_id=pr_id, merge_method=merge_method)
        return result.get("mergePullRequest", {}).get("pullRequest", {})

    async def close_pull_request(self, pr_id: str) -> Dict[str, Any]:
        result = await self._execute(CLOSE_PR_MUTATION, {"prId": pr_id})
        logger.info("closed_pull_request", pr_id=pr_id)
        return result.get("closePullRequest", {}).get("pullRequest", {})

    async def add_comment(self, pr_id: str, body: str) -> Dict[str, Any]:
        result = await self._execute(ADD_COMMENT_MUTATION, {"prId": pr_id, "body": body})
        logger.info("added_comment", pr_id=pr_id, body_preview=body[:60])
        return result.get("addComment", {})

    async def add_labels(self, owner: str, repo: str, pr_id: str, label_names: List[str]) -> Dict[str, Any]:
        """Resolve label names → IDs, then add them to the PR in one mutation."""
        labels_result = await self._execute(GET_LABEL_IDS_QUERY, {"owner": owner, "repo": repo})
        all_labels = labels_result.get("repository", {}).get("labels", {}).get("nodes", [])
        label_ids = [l["id"] for l in all_labels if l["name"] in label_names]

        if not label_ids:
            available = [l["name"] for l in all_labels]
            logger.warning("no_matching_labels_found", requested=label_names, available=available)
            return {}

        result = await self._execute(ADD_LABELS_MUTATION, {"labelableId": pr_id, "labelIds": label_ids})
        logger.info("added_labels", pr_id=pr_id, labels=label_names)
        return result.get("addLabelsToLabelable", {})

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _execute(self, query, variables: Dict[str, Any]) -> Dict[str, Any]:
        try:
            async with self._client as session:
                return await session.execute(query, variable_values=variables)
        except TransportQueryError as exc:
            logger.error("github_graphql_error", errors=exc.errors)
            raise
