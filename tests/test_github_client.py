"""
Unit tests for src/github/client.py

All network I/O is mocked — gql.Client.execute is patched so no real
HTTP calls are made.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from github.client import GitHubGraphQLClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client() -> GitHubGraphQLClient:
    return GitHubGraphQLClient(endpoint="https://api.github.com/graphql", token="ghp_test")


def _prs_response(nodes):
    return {"repository": {"pullRequests": {"nodes": nodes, "pageInfo": {"hasNextPage": False}}}}


def _labels_response(labels):
    return {"repository": {"labels": {"nodes": labels}}}


# ---------------------------------------------------------------------------
# get_pull_requests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_pull_requests_returns_nodes(pr_open):
    client = _make_client()
    with patch.object(client, "_execute", new=AsyncMock(return_value=_prs_response([pr_open]))):
        prs = await client.get_pull_requests("acme", "payment-service", labels=["auto-merge"])

    assert len(prs) == 1
    assert prs[0]["number"] == 42


@pytest.mark.asyncio
async def test_get_pull_requests_empty_repo():
    client = _make_client()
    with patch.object(client, "_execute", new=AsyncMock(return_value=_prs_response([]))):
        prs = await client.get_pull_requests("acme", "empty-repo")

    assert prs == []


@pytest.mark.asyncio
async def test_get_pull_requests_passes_labels_and_states():
    client = _make_client()
    captured = {}

    async def capture(query, variables):
        captured.update(variables)
        return _prs_response([])

    with patch.object(client, "_execute", new=capture):
        await client.get_pull_requests("acme", "repo", labels=["my-label"], states=["OPEN"])

    assert captured["labels"] == ["my-label"]
    assert captured["states"] == ["OPEN"]


@pytest.mark.asyncio
async def test_get_pull_requests_defaults_to_open_state():
    client = _make_client()
    captured = {}

    async def capture(query, variables):
        captured.update(variables)
        return _prs_response([])

    with patch.object(client, "_execute", new=capture):
        await client.get_pull_requests("acme", "repo")

    assert captured["states"] == ["OPEN"]


# ---------------------------------------------------------------------------
# merge_pull_request
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_merge_pull_request_calls_mutation():
    client = _make_client()
    response = {"mergePullRequest": {"pullRequest": {"number": 42, "merged": True, "mergedAt": "2026-02-27T00:00:00Z"}}}
    captured = {}

    async def capture(query, variables):
        captured.update(variables)
        return response

    with patch.object(client, "_execute", new=capture):
        result = await client.merge_pull_request("PR_abc123", "SQUASH")

    assert captured["prId"] == "PR_abc123"
    assert captured["mergeMethod"] == "SQUASH"
    assert result["merged"] is True


@pytest.mark.asyncio
async def test_merge_pull_request_default_method_is_squash():
    client = _make_client()
    captured = {}

    async def capture(query, variables):
        captured.update(variables)
        return {"mergePullRequest": {"pullRequest": {}}}

    with patch.object(client, "_execute", new=capture):
        await client.merge_pull_request("PR_abc123")

    assert captured["mergeMethod"] == "SQUASH"


# ---------------------------------------------------------------------------
# close_pull_request
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_close_pull_request():
    client = _make_client()
    response = {"closePullRequest": {"pullRequest": {"number": 42, "closed": True}}}

    with patch.object(client, "_execute", new=AsyncMock(return_value=response)):
        result = await client.close_pull_request("PR_abc123")

    assert result["closed"] is True


# ---------------------------------------------------------------------------
# add_comment
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_comment_sends_body():
    client = _make_client()
    captured = {}
    response = {"addComment": {"commentEdge": {"node": {"id": "IC_1", "body": "hello"}}}}

    async def capture(query, variables):
        captured.update(variables)
        return response

    with patch.object(client, "_execute", new=capture):
        await client.add_comment("PR_abc123", "hello")

    assert captured["prId"] == "PR_abc123"
    assert captured["body"] == "hello"


# ---------------------------------------------------------------------------
# add_labels
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_labels_resolves_ids_then_mutates():
    client = _make_client()
    calls = []

    async def fake_execute(query, variables):
        calls.append(variables)
        if "owner" in variables:
            # First call: label ID resolution
            return _labels_response([{"id": "LA_1", "name": "needs-human-approval"}])
        # Second call: mutation
        return {"addLabelsToLabelable": {}}

    with patch.object(client, "_execute", new=fake_execute):
        await client.add_labels("acme", "repo", "PR_abc123", ["needs-human-approval"])

    assert len(calls) == 2
    assert calls[1]["labelIds"] == ["LA_1"]


@pytest.mark.asyncio
async def test_add_labels_skips_mutation_when_no_match():
    client = _make_client()
    mutation_called = False

    async def fake_execute(query, variables):
        nonlocal mutation_called
        if "owner" in variables:
            return _labels_response([{"id": "LA_1", "name": "other-label"}])
        mutation_called = True
        return {}

    with patch.object(client, "_execute", new=fake_execute):
        result = await client.add_labels("acme", "repo", "PR_abc123", ["needs-human-approval"])

    assert result == {}
    assert not mutation_called
