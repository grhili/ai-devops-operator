# AI DevOps Operator - MCP API Specification

**Version:** 1.0
**Last Updated:** 2026-02-27
**Status:** Production-Ready Specification

---

## Table of Contents

1. [Introduction](#introduction)
2. [GitHub MCP API](#github-mcp-api)
3. [Kubernetes MCP API](#kubernetes-mcp-api)
4. [Argo CD MCP API](#argo-cd-mcp-api)
5. [Common Data Types](#common-data-types)
6. [Error Handling](#error-handling)
7. [Idempotency Guarantees](#idempotency-guarantees)
8. [Rate Limiting](#rate-limiting)

---

## 1. Introduction

This document provides complete OpenAPI-style specifications for all Model Context Protocol (MCP) calls used by the AI DevOps Operator. Each API call includes:
- Parameter schemas with types and validation rules
- Return type definitions
- Error codes and meanings
- Idempotency guarantees
- Usage examples

### Conventions

- **Required parameters:** Must be provided in every call
- **Optional parameters:** Can be omitted, defaults apply
- **Validation:** All parameters are validated before MCP call execution
- **Error handling:** See Section 6 for retry policies and error recovery

---

## 2. GitHub MCP API

### 2.1 readRepositoryContent

Reads file content from a GitHub repository.

**API Call:** `github.readRepositoryContent`

#### Parameters

```yaml
repo:
  type: string
  pattern: "^[a-z0-9_-]+/[a-z0-9_-]+$"
  required: true
  description: "Repository in format 'owner/repo'"
  example: "my-org/payment-service"

path:
  type: string
  required: true
  description: "File path within repository"
  example: "kubernetes/deployment.yaml"

ref:
  type: string
  required: false
  default: "HEAD"
  description: "Git ref (branch, tag, or commit SHA)"
  example: "main" | "v1.2.3" | "abc123def456"
```

#### Returns

**Success:**
```yaml
content:
  type: string
  description: "Base64-decoded file content"
  example: "apiVersion: apps/v1\nkind: Deployment..."

sha:
  type: string
  description: "Git blob SHA of the file"
  example: "abc123def456"

encoding:
  type: string
  enum: ["utf-8", "base64"]
  description: "Content encoding"
```

**Errors:**
```yaml
NOT_FOUND:
  httpStatus: 404
  description: "Repository or file path does not exist"
  recovery: "Abort operation, alert human"

PERMISSION_DENIED:
  httpStatus: 403
  description: "AI operator lacks read access to repository"
  recovery: "Escalate to human, check service account permissions"

NETWORK_ERROR:
  httpStatus: 502/503/504
  description: "Temporary network or GitHub API issue"
  recovery: "Retry with exponential backoff (3 attempts)"
```

#### Example

```python
# Request
response = mcp.call("github.readRepositoryContent", {
    "repo": "my-org/payment-service",
    "path": ".argocd/application.yaml",
    "ref": "main"
})

# Success Response
{
    "content": "apiVersion: argoproj.io/v1alpha1\n...",
    "sha": "abc123",
    "encoding": "utf-8"
}

# Error Response
{
    "error": "NOT_FOUND",
    "message": "File .argocd/application.yaml not found in my-org/payment-service"
}
```

---

### 2.2 createPullRequest

Creates a new pull request with file changes.

**API Call:** `github.createPullRequest`

#### Parameters

```yaml
repo:
  type: string
  pattern: "^[a-z0-9_-]+/[a-z0-9_-]+$"
  required: true
  description: "Repository in format 'owner/repo'"

sourceBranch:
  type: string
  pattern: "^[a-z0-9/_-]+$"
  required: true
  description: "Branch name for the PR (will be created if doesn't exist)"
  example: "rollback/payment-service-abc123"

targetBranch:
  type: string
  enum: ["main", "staging", "develop"]
  required: true
  description: "Base branch to merge into"
  validation: "Must be a protected branch in repository settings"

baseSha:
  type: string
  pattern: "^[a-f0-9]{40}$"
  required: true
  description: "SHA of target branch HEAD (prevents race conditions)"

changes:
  type: array
  items: FileChange
  required: true
  minItems: 1
  maxItems: 50
  description: "List of file changes to include in PR"

title:
  type: string
  required: true
  minLength: 10
  maxLength: 200
  description: "PR title (should start with 'Rollback:' for rollback PRs)"
  example: "Rollback: payment-service to abc123 (degraded in staging)"

description:
  type: string
  format: markdown
  required: true
  minLength: 50
  description: "Detailed PR description with context, reasoning, and checklist"

labels:
  type: array
  items: string
  required: false
  default: []
  description: "GitHub labels to apply to PR"
  example: ["ai-operator", "rollback", "staging"]

draft:
  type: boolean
  required: false
  default: false
  description: "Create as draft PR"
```

#### FileChange Type

```yaml
path:
  type: string
  required: true
  description: "File path within repository"

content:
  type: string
  required: true
  description: "New file content (full file, not diff)"

encoding:
  type: string
  enum: ["utf-8", "base64"]
  default: "utf-8"
```

#### Returns

**Success:**
```yaml
id:
  type: integer
  description: "GitHub PR ID (internal)"
  example: 123456789

number:
  type: integer
  description: "PR number (user-facing)"
  example: 42

url:
  type: string
  format: uri
  description: "Full URL to the PR"
  example: "https://github.com/my-org/payment-service/pull/42"

state:
  type: string
  enum: ["open"]
  description: "Initial state is always 'open'"

sourceBranch:
  type: string
  description: "Confirmed source branch name"

targetBranch:
  type: string
  description: "Confirmed target branch name"
```

**Errors:**
```yaml
BRANCH_NOT_FOUND:
  httpStatus: 404
  description: "Target branch does not exist"
  recovery: "Abort rollback, alert human"

BASE_SHA_MISMATCH:
  httpStatus: 409
  description: "Target branch HEAD changed since baseSha was read"
  recovery: "Re-read target branch SHA, retry once"

PERMISSION_DENIED:
  httpStatus: 403
  description: "AI operator lacks write access to repository"
  recovery: "Escalate to human, check service account permissions"

CONFLICT:
  httpStatus: 409
  description: "File changes conflict with target branch"
  recovery: "Abort rollback, alert human with conflict details"

NETWORK_ERROR:
  httpStatus: 502/503/504
  description: "Temporary network or GitHub API issue"
  recovery: "Retry with exponential backoff (3 attempts)"

VALIDATION_ERROR:
  httpStatus: 400
  description: "Invalid parameters (e.g., title too long, no changes)"
  recovery: "Log error, abort operation"
```

#### Idempotency

**Not Idempotent** - Each call creates a new PR.

**Best Practice:** Before calling `createPullRequest`, call `listOpenPullRequests` with the target branch to check for existing PRs. If a PR already exists for the same rollback (check title pattern), reuse it instead of creating duplicate.

#### Example

```python
# Request
response = mcp.call("github.createPullRequest", {
    "repo": "my-org/payment-service",
    "sourceBranch": "rollback/payment-service-abc123",
    "targetBranch": "staging",
    "baseSha": "def456abc789def456abc789def456abc789def456",
    "changes": [
        {
            "path": ".argocd/application.yaml",
            "content": "apiVersion: argoproj.io/v1alpha1\n...",
            "encoding": "utf-8"
        }
    ],
    "title": "Rollback: payment-service to abc123 (degraded in staging)",
    "description": "## Automated Rollback\n\n**Triggered by:** AI DevOps Operator...",
    "labels": ["ai-operator", "rollback", "staging"]
})

# Success Response
{
    "id": 123456789,
    "number": 42,
    "url": "https://github.com/my-org/payment-service/pull/42",
    "state": "open",
    "sourceBranch": "rollback/payment-service-abc123",
    "targetBranch": "staging"
}
```

---

### 2.3 getPullRequestStatus

Gets current status of a pull request.

**API Call:** `github.getPullRequestStatus`

#### Parameters

```yaml
repo:
  type: string
  pattern: "^[a-z0-9_-]+/[a-z0-9_-]+$"
  required: true

prNumber:
  type: integer
  required: true
  minimum: 1
  description: "PR number (not internal ID)"
```

#### Returns

**Success:**
```yaml
number:
  type: integer
  description: "PR number"

state:
  type: string
  enum: ["open", "closed", "merged"]
  description: "Current PR state"

mergeable:
  type: boolean
  description: "Whether PR can be merged (no conflicts, branch protection satisfied)"

mergeableState:
  type: string
  enum: ["clean", "unstable", "dirty", "blocked", "unknown"]
  description: "Detailed mergeability status"

ciStatus:
  type: string
  enum: ["pending", "success", "failure", "error"]
  description: "Aggregated CI/CD check status"

ciChecks:
  type: array
  items: CICheck
  description: "Individual CI check details"

approvals:
  type: integer
  minimum: 0
  description: "Number of approving reviews"

requiredApprovals:
  type: integer
  minimum: 0
  description: "Number of approvals required by branch protection"

reviewers:
  type: array
  items: string
  description: "GitHub usernames of reviewers"

mergeable_state:
  type: string
  description: "GitHub's assessment: 'behind', 'blocked', 'clean', 'dirty', 'unstable', 'unknown'"
```

#### CICheck Type

```yaml
name:
  type: string
  description: "Check name"
  example: "ci/circleci: test"

status:
  type: string
  enum: ["pending", "success", "failure", "error"]

conclusion:
  type: string
  enum: ["success", "failure", "neutral", "cancelled", "timed_out", "action_required"]

detailsUrl:
  type: string
  format: uri
  description: "Link to CI run details"
```

**Errors:**
```yaml
NOT_FOUND:
  httpStatus: 404
  description: "PR does not exist"
  recovery: "Abort operation, log error"

PERMISSION_DENIED:
  httpStatus: 403
  description: "AI operator lacks read access"
  recovery: "Escalate to human"
```

#### Example

```python
# Request
response = mcp.call("github.getPullRequestStatus", {
    "repo": "my-org/payment-service",
    "prNumber": 42
})

# Success Response
{
    "number": 42,
    "state": "open",
    "mergeable": true,
    "mergeableState": "clean",
    "ciStatus": "success",
    "ciChecks": [
        {"name": "ci/tests", "status": "success", "conclusion": "success"},
        {"name": "ci/lint", "status": "success", "conclusion": "success"}
    ],
    "approvals": 0,
    "requiredApprovals": 1,
    "reviewers": []
}
```

---

### 2.4 mergePullRequest

Merges a pull request.

**API Call:** `github.mergePullRequest`

#### Parameters

```yaml
repo:
  type: string
  pattern: "^[a-z0-9_-]+/[a-z0-9_-]+$"
  required: true

prNumber:
  type: integer
  required: true
  minimum: 1

mergeMethod:
  type: string
  enum: ["merge", "squash", "rebase"]
  required: false
  default: "squash"
  description: "Merge strategy"

commitTitle:
  type: string
  required: false
  description: "Custom merge commit title (defaults to PR title)"

commitMessage:
  type: string
  required: false
  description: "Custom merge commit message (defaults to PR description)"
```

#### Preconditions

**The following must be true or call will fail:**
- `ciStatus == "success"` (all CI checks passed)
- `mergeable == true` (no conflicts)
- `approvals >= requiredApprovals` (sufficient reviews)
- `state == "open"` (PR not already closed/merged)

**Recommendation:** Call `getPullRequestStatus` first to verify preconditions.

#### Returns

**Success:**
```yaml
merged:
  type: boolean
  description: "Always true on success"

sha:
  type: string
  pattern: "^[a-f0-9]{40}$"
  description: "SHA of the merge commit"

message:
  type: string
  description: "Confirmation message"
  example: "Pull request successfully merged"
```

**Errors:**
```yaml
NOT_MERGEABLE:
  httpStatus: 405
  description: "PR cannot be merged (conflicts, failed CI, or insufficient approvals)"
  recovery: "Check getPullRequestStatus for details, abort auto-merge, alert human"

PERMISSION_DENIED:
  httpStatus: 403
  description: "AI operator lacks merge permissions"
  recovery: "Escalate to human"

CI_PENDING:
  httpStatus: 409
  description: "CI checks still running"
  recovery: "Wait and poll, timeout after 300s"

ALREADY_MERGED:
  httpStatus: 405
  description: "PR already merged"
  recovery: "Treat as success, continue to health monitoring"

NOT_FOUND:
  httpStatus: 404
  description: "PR does not exist"
  recovery: "Abort operation"
```

#### Example

```python
# Request
response = mcp.call("github.mergePullRequest", {
    "repo": "my-org/payment-service",
    "prNumber": 42,
    "mergeMethod": "squash"
})

# Success Response
{
    "merged": true,
    "sha": "abc123def456abc123def456abc123def456abc1",
    "message": "Pull request successfully merged"
}
```

---

### 2.5 listOpenPullRequests

Lists open pull requests for a repository.

**API Call:** `github.listOpenPullRequests`

#### Parameters

```yaml
repo:
  type: string
  pattern: "^[a-z0-9_-]+/[a-z0-9_-]+$"
  required: true

targetBranch:
  type: string
  required: false
  description: "Filter by target branch (e.g., 'staging')"

labels:
  type: array
  items: string
  required: false
  description: "Filter by labels (PR must have all specified labels)"

author:
  type: string
  required: false
  description: "Filter by PR author username"
```

#### Returns

**Success:**
```yaml
prs:
  type: array
  items: PullRequestSummary
  description: "List of open PRs matching filters"
```

#### PullRequestSummary Type

```yaml
number:
  type: integer
  description: "PR number"

title:
  type: string
  description: "PR title"

sourceBranch:
  type: string
  description: "Source branch name"

targetBranch:
  type: string
  description: "Target branch name"

author:
  type: string
  description: "GitHub username of PR author"

createdAt:
  type: string
  format: date-time
  description: "ISO 8601 timestamp"

labels:
  type: array
  items: string
  description: "List of label names"

url:
  type: string
  format: uri
  description: "Full URL to PR"
```

#### Example

```python
# Request
response = mcp.call("github.listOpenPullRequests", {
    "repo": "my-org/payment-service",
    "targetBranch": "staging",
    "labels": ["ai-operator", "rollback"]
})

# Success Response
{
    "prs": [
        {
            "number": 42,
            "title": "Rollback: payment-service to abc123",
            "sourceBranch": "rollback/payment-service-abc123",
            "targetBranch": "staging",
            "author": "ai-operator-bot",
            "createdAt": "2026-02-27T10:30:00Z",
            "labels": ["ai-operator", "rollback", "staging"],
            "url": "https://github.com/my-org/payment-service/pull/42"
        }
    ]
}
```

---

## 3. Kubernetes MCP API

### 3.1 getDeploymentStatus

Gets status of a Kubernetes deployment.

**API Call:** `kubernetes.getDeploymentStatus`

#### Parameters

```yaml
namespace:
  type: string
  pattern: "^[a-z0-9-]+$"
  required: true
  description: "Kubernetes namespace"
  example: "production"

deploymentName:
  type: string
  pattern: "^[a-z0-9-]+$"
  required: true
  description: "Deployment resource name"
  example: "payment-service"
```

#### Returns

**Success:**
```yaml
name:
  type: string
  description: "Deployment name"

namespace:
  type: string
  description: "Namespace"

desiredReplicas:
  type: integer
  minimum: 0
  description: "Number of replicas specified in deployment spec"

availableReplicas:
  type: integer
  minimum: 0
  description: "Number of available replicas (passing readiness probe)"

readyReplicas:
  type: integer
  minimum: 0
  description: "Number of ready replicas"

updatedReplicas:
  type: integer
  minimum: 0
  description: "Number of replicas at current revision"

conditions:
  type: array
  items: DeploymentCondition
  description: "Deployment conditions"

observedGeneration:
  type: integer
  description: "Generation observed by controller"

generation:
  type: integer
  description: "Current generation in spec"
```

#### DeploymentCondition Type

```yaml
type:
  type: string
  enum: ["Available", "Progressing", "ReplicaFailure"]

status:
  type: string
  enum: ["True", "False", "Unknown"]

reason:
  type: string
  description: "Machine-readable reason code"

message:
  type: string
  description: "Human-readable message"

lastTransitionTime:
  type: string
  format: date-time
```

**Errors:**
```yaml
NOT_FOUND:
  httpStatus: 404
  description: "Deployment does not exist in namespace"
  recovery: "Abort operation, alert human"

PERMISSION_DENIED:
  httpStatus: 403
  description: "Service account lacks get deployments permission"
  recovery: "Escalate to human, check RBAC"

NETWORK_ERROR:
  httpStatus: 502/503
  description: "Cannot connect to Kubernetes API"
  recovery: "Retry with exponential backoff (3 attempts)"
```

#### Example

```python
# Request
response = mcp.call("kubernetes.getDeploymentStatus", {
    "namespace": "production",
    "deploymentName": "payment-service"
})

# Success Response
{
    "name": "payment-service",
    "namespace": "production",
    "desiredReplicas": 3,
    "availableReplicas": 1,
    "readyReplicas": 1,
    "updatedReplicas": 1,
    "conditions": [
        {
            "type": "Available",
            "status": "True",
            "reason": "MinimumReplicasAvailable",
            "message": "Deployment has minimum availability."
        },
        {
            "type": "Progressing",
            "status": "False",
            "reason": "ProgressDeadlineExceeded",
            "message": "ReplicaSet has timed out progressing."
        }
    ]
}
```

---

### 3.2 listPods

Lists pods in a namespace.

**API Call:** `kubernetes.listPods`

#### Parameters

```yaml
namespace:
  type: string
  pattern: "^[a-z0-9-]+$"
  required: true

labelSelector:
  type: string
  required: false
  description: "Label selector in Kubernetes format"
  example: "app=payment-service,version=v1.2.3"

fieldSelector:
  type: string
  required: false
  description: "Field selector (e.g., 'status.phase=Running')"
```

#### Returns

**Success:**
```yaml
pods:
  type: array
  items: PodSummary
  description: "List of pods matching selectors"
```

#### PodSummary Type

```yaml
name:
  type: string

namespace:
  type: string

phase:
  type: string
  enum: ["Pending", "Running", "Succeeded", "Failed", "Unknown"]

conditions:
  type: array
  items: PodCondition

containerStatuses:
  type: array
  items: ContainerStatus

labels:
  type: object
  additionalProperties: string

createdAt:
  type: string
  format: date-time
```

#### Example

```python
# Request
response = mcp.call("kubernetes.listPods", {
    "namespace": "production",
    "labelSelector": "app=payment-service"
})

# Success Response
{
    "pods": [
        {
            "name": "payment-service-7d8f9c-abc12",
            "namespace": "production",
            "phase": "Running",
            "conditions": [...],
            "containerStatuses": [...],
            "labels": {"app": "payment-service", "version": "v1.2.3"}
        }
    ]
}
```

---

## 4. Argo CD MCP API

### 4.1 getApplicationHealth

Gets health status of an Argo CD application.

**API Call:** `argocd.getApplicationHealth`

#### Parameters

```yaml
appName:
  type: string
  pattern: "^[a-z0-9-]+$"
  required: true
  description: "Argo CD application name"
  example: "payment-service"

namespace:
  type: string
  required: false
  default: "argocd"
  description: "Argo CD namespace (if not default)"
```

#### Returns

**Success:**
```yaml
appName:
  type: string

status:
  type: string
  enum: ["Healthy", "Progressing", "Degraded", "Suspended", "Missing", "Unknown"]
  description: "Overall health status"

message:
  type: string
  description: "Human-readable health message"

resources:
  type: array
  items: ResourceHealth
  description: "Per-resource health details"
```

#### ResourceHealth Type

```yaml
kind:
  type: string
  description: "Kubernetes resource kind"
  example: "Deployment"

name:
  type: string
  description: "Resource name"

namespace:
  type: string
  description: "Resource namespace"

status:
  type: string
  enum: ["Healthy", "Progressing", "Degraded", "Suspended", "Missing", "Unknown"]

message:
  type: string
  description: "Health message for this specific resource"
```

**Errors:**
```yaml
NOT_FOUND:
  httpStatus: 404
  description: "Argo CD application does not exist"
  recovery: "Abort operation, verify app name"

PERMISSION_DENIED:
  httpStatus: 403
  description: "Service account lacks Argo CD API access"
  recovery: "Escalate to human, check RBAC"

NETWORK_ERROR:
  httpStatus: 502/503
  description: "Cannot connect to Argo CD API"
  recovery: "Retry with exponential backoff (3 attempts)"
```

#### Example

```python
# Request
response = mcp.call("argocd.getApplicationHealth", {
    "appName": "payment-service"
})

# Success Response
{
    "appName": "payment-service",
    "status": "Degraded",
    "message": "Deployment payment-service has insufficient replicas",
    "resources": [
        {
            "kind": "Deployment",
            "name": "payment-service",
            "namespace": "production",
            "status": "Degraded",
            "message": "1/3 replicas available"
        },
        {
            "kind": "Service",
            "name": "payment-service",
            "namespace": "production",
            "status": "Healthy",
            "message": "service is healthy"
        }
    ]
}
```

---

### 4.2 getApplicationSyncStatus

Gets sync status of an Argo CD application.

**API Call:** `argocd.getApplicationSyncStatus`

#### Parameters

```yaml
appName:
  type: string
  pattern: "^[a-z0-9-]+$"
  required: true
```

#### Returns

**Success:**
```yaml
appName:
  type: string

status:
  type: string
  enum: ["Synced", "OutOfSync"]
  description: "Whether live state matches desired state"

revision:
  type: string
  pattern: "^[a-f0-9]{40}$"
  description: "Git commit SHA currently synced"

targetRevision:
  type: string
  description: "Target revision from Argo CD app spec (e.g., 'HEAD', 'main')"

syncedAt:
  type: string
  format: date-time
  description: "Timestamp of last successful sync"
```

#### Example

```python
# Request
response = mcp.call("argocd.getApplicationSyncStatus", {
    "appName": "payment-service"
})

# Success Response
{
    "appName": "payment-service",
    "status": "Synced",
    "revision": "abc123def456abc123def456abc123def456abc1",
    "targetRevision": "main",
    "syncedAt": "2026-02-27T10:00:00Z"
}
```

---

## 5. Common Data Types

### 5.1 Error Response Schema

All MCP calls return errors in this format:

```yaml
error:
  type: string
  description: "Error code (e.g., 'NOT_FOUND', 'PERMISSION_DENIED')"

message:
  type: string
  description: "Human-readable error message"

httpStatus:
  type: integer
  description: "HTTP status code"

retryable:
  type: boolean
  description: "Whether error is transient and retryable"

details:
  type: object
  description: "Additional error context (optional)"
```

**Example:**
```json
{
    "error": "PERMISSION_DENIED",
    "message": "Service account ai-operator lacks 'pull_requests:write' scope",
    "httpStatus": 403,
    "retryable": false,
    "details": {
        "requiredScope": "pull_requests:write",
        "currentScopes": ["contents:read", "pull_requests:read"]
    }
}
```

---

## 6. Error Handling

### 6.1 Retry Policy

**Retryable Errors:**
- `NETWORK_ERROR` (502, 503, 504)
- `RATE_LIMIT_EXCEEDED` (429)
- Timeouts

**Non-Retryable Errors:**
- `PERMISSION_DENIED` (403)
- `NOT_FOUND` (404)
- `VALIDATION_ERROR` (400)
- `CONFLICT` (409)

**Retry Configuration:**
```yaml
maxRetries: 3
backoff: exponential
initialDelay: 1s
maxDelay: 10s
multiplier: 2
```

**Backoff Schedule:**
1. First retry: 1s delay
2. Second retry: 2s delay
3. Third retry: 4s delay
4. After 3 failures: escalate to error handling

### 6.2 Error Recovery by MCP Call

| MCP Call | Error | Recovery Action |
|----------|-------|----------------|
| `createPullRequest` | `PERMISSION_DENIED` | Abort rollback, escalate to human |
| `createPullRequest` | `CONFLICT` | Retry once with rebase, then abort |
| `createPullRequest` | `NETWORK_ERROR` | Retry 3 times with backoff |
| `mergePullRequest` | `NOT_MERGEABLE` | Check status, abort auto-merge, alert human |
| `mergePullRequest` | `CI_PENDING` | Poll for 300s, then abort |
| `getApplicationHealth` | `NOT_FOUND` | Abort operation, verify app name |
| `getDeploymentStatus` | `NETWORK_ERROR` | Retry 3 times, if still failing skip this check |

---

## 7. Idempotency Guarantees

### 7.1 Idempotent Calls

These calls can be safely retried without side effects:

- `readRepositoryContent` - Read-only
- `getPullRequestStatus` - Read-only
- `listOpenPullRequests` - Read-only
- `getDeploymentStatus` - Read-only
- `listPods` - Read-only
- `getApplicationHealth` - Read-only
- `getApplicationSyncStatus` - Read-only

### 7.2 Non-Idempotent Calls

These calls have side effects and require careful handling:

**`createPullRequest`:**
- Each call creates a **new** PR
- **Mitigation:** Call `listOpenPullRequests` first to check for existing PRs
- **Pattern:**
  ```python
  existing = listOpenPullRequests(repo, targetBranch, labels=["ai-operator"])
  if existing.prs:
      pr = existing.prs[0]  # Reuse existing
  else:
      pr = createPullRequest(...)  # Create new
  ```

**`mergePullRequest`:**
- Attempting to merge an already-merged PR returns `ALREADY_MERGED` error
- **Mitigation:** Check `getPullRequestStatus().state` before calling
- **Safe retry:** If error is `ALREADY_MERGED`, treat as success

---

## 8. Rate Limiting

### 8.1 GitHub API Rate Limits

**Authenticated requests:** 5000 requests/hour
**Typical AI Operator usage:** ~100 requests/hour (well within limits)

**Per-call costs:**
- `readRepositoryContent`: 1 request
- `createPullRequest`: 1 request
- `getPullRequestStatus`: 1 request
- `mergePullRequest`: 1 request
- `listOpenPullRequests`: 1 request

**Rate limit handling:**
- Monitor `X-RateLimit-Remaining` header
- If `RATE_LIMIT_EXCEEDED` error: wait for `X-RateLimit-Reset` timestamp
- Log warning if rate limit < 1000 remaining

### 8.2 Kubernetes API Rate Limits

**Default:** No hard rate limits (cluster-dependent)
**Best practice:** Limit polling to 1 request per resource per 10 seconds

**AI Operator compliance:**
- `getDeploymentStatus`: Called every 10s per monitored app
- `listPods`: Called only during degradation investigation (infrequent)

### 8.3 Argo CD API Rate Limits

**Default:** No hard rate limits
**Best practice:** Limit health checks to 1 per app per 10 seconds

**AI Operator compliance:**
- `getApplicationHealth`: Called every 10s per monitored app
- `getApplicationSyncStatus`: Called once per rollback attempt

---

## Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-27 | AI DevOps Team | Initial production-ready specification |

---

**Related Documents:**
- [Architecture](./ai-operator-architecture.md) - System overview
- [Governance Specification](./ai-operator-governance-spec.md) - How MCP calls are governed
- [Rollback Engine](./ai-rollback-engine-spec.md) - How MCP calls are orchestrated
