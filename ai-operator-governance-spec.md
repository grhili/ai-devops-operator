# AI Operator Governance & Level 2 Autonomy Specification

**Version:** 2.0
**Last Updated:** 2026-02-27
**Status:** Production-Ready Governance Framework
**Audience:** Security Teams, Compliance Officers, AI Implementation Engineers

---

## Table of Contents

1. [Overview](#1-overview)
2. [Behavioral Rules and Limitations](#2-behavioral-rules-and-limitations)
3. [MCP Call Permissions](#3-mcp-call-permissions)
4. [Level 2 Autonomy Invariants](#4-level-2-autonomy-invariants)
5. [Audit Logging Specification](#5-audit-logging-specification)
6. [Error Handling and Retry Logic](#6-error-handling-and-retry-logic)
7. [Human Escalation Procedure](#7-human-escalation-procedure)
8. [RBAC Model](#8-rbac-model)
9. [Rate Limiting and Quotas](#9-rate-limiting-and-quotas)
10. [Compliance and Security](#10-compliance-and-security)

---

## 1. Overview

This document defines the **governance framework** for the AI DevOps Operator, establishing:
- Strict behavioral rules and limitations
- Complete MCP parameter schemas with validation
- Quantitative invariant definitions for Level 2 autonomy
- Immutable audit logging requirements
- Error handling and human escalation procedures
- RBAC model and permission boundaries

**Core Principle:** The AI Operator operates at **Level 2 Autonomy** - it can execute actions autonomously in staging environments under strict safety conditions, but requires human approval for all production changes.

**Prerequisites:** Read [`ai-operator-architecture.md`](./ai-operator-architecture.md) first for system context.

---

## 2. Behavioral Rules and Limitations

### 2.1 General Principles

| Rule ID | Principle | Enforcement |
|---------|-----------|-------------|
| **GR-01** | Always follow least-privilege access principles | RBAC model (Section 8) |
| **GR-02** | Never bypass Git-based workflows for any production changes | MCP call restrictions (Section 3) |
| **GR-03** | Never modify Kubernetes cluster state directly (read-only access) | RBAC model, no write permissions |
| **GR-04** | Always verify all formal safety invariants before auto-merge | Invariant enforcement (Section 4) |
| **GR-05** | Produce structured, immutable logs for every action | Audit logging (Section 5) |
| **GR-06** | Refuse any request or action that violates governance policies | FSM guards, invariant checks |
| **GR-07** | When ambiguity exists, default to safest interpretation | Fail-safe defaults (abort and alert) |
| **GR-08** | Never escalate own permissions or modify governance configurations | MCP call restrictions |
| **GR-09** | All actions must be traceable through PRs and audit logs | Audit logging, PR-based workflow |
| **GR-10** | Production merges require explicit human confirmation (no exceptions) | Environment checks, FSM guards |

### 2.2 Pull Request Management Rules

| Rule ID | Description | Applies To |
|---------|-------------|------------|
| **PR-01** | MAY create and update pull requests to propose changes | All environments |
| **PR-02** | MUST NOT push directly to protected branches | All environments |
| **PR-03** | MUST NOT force-merge or bypass branch protection rules | All environments |
| **PR-04** | MAY auto-merge rollback PRs ONLY in staging under Level 2 conditions | Staging only |
| **PR-05** | MUST verify CI status == "success" before any merge | All environments |
| **PR-06** | MUST verify all 8 invariants pass before auto-merge | Staging auto-merge |
| **PR-07** | MUST abort merge attempt if any invariant fails | All environments |
| **PR-08** | MUST close PR if health restores before merge | All environments |
| **PR-09** | MUST NOT create duplicate PRs for same rollback | All environments |
| **PR-10** | Production merges require human approval (1 senior maintainer) | Production only |

### 2.3 Repository Interaction Rules

| Rule ID | Description | Permission Required |
|---------|-------------|---------------------|
| **RI-01** | MAY read repository content as needed | `contents:read` |
| **RI-02** | MAY create, update, comment on pull requests | `pull_requests:write` |
| **RI-03** | MUST NOT modify files directly on protected branches | N/A (blocked) |
| **RI-04** | MUST NOT delete branches, tags, or releases | N/A (blocked) |
| **RI-05** | MUST NOT modify repository settings or webhooks | N/A (blocked) |
| **RI-06** | All changes must be traceable through PRs | `pull_requests:write` |

### 2.4 Cluster State Reading Rules

| Rule ID | Description | Permission Required |
|---------|-------------|---------------------|
| **CR-01** | MAY read Kubernetes cluster state (read-only) | `get`, `list` on deployments/pods |
| **CR-02** | MUST NOT delete namespaces, workloads, or resources | N/A (blocked) |
| **CR-03** | MUST NOT modify any Kubernetes resources | N/A (blocked) |
| **CR-04** | MAY read Argo CD application health and sync status | `applications:get` |
| **CR-05** | MUST NOT trigger Argo CD sync operations | N/A (blocked) |
| **CR-06** | Access scoped to specific namespaces only | `namespace: production, staging` |

---

## 3. MCP Call Permissions

This section provides **complete parameter schemas** for all allowed MCP calls, including validation rules and constraints.

**Reference:** See [`ai-operator-mcp-api-spec.md`](./ai-operator-mcp-api-spec.md) for detailed API specifications.

### 3.1 GitHub MCP Calls

#### readRepositoryContent

**Permission:** `contents:read`

**Parameters:**
```yaml
repo:
  type: string
  pattern: "^[a-z0-9_-]+/[a-z0-9_-]+$"
  required: true
  allowedValues: ["my-org/*"]  # Organization-scoped

path:
  type: string
  required: true
  maxLength: 500

ref:
  type: string
  required: false
  default: "HEAD"
```

**Validation:**
- `repo` must match organization pattern
- `path` must not contain `..` (prevent directory traversal)

**Use Cases:**
- Read `.argocd/application.yaml` to get current revision
- Read deployment manifests for context

---

#### createPullRequest

**Permission:** `pull_requests:write`

**Parameters:**
```yaml
repo:
  type: string
  pattern: "^[a-z0-9_-]+/[a-z0-9_-]+$"
  required: true
  allowedValues: ["my-org/*"]

sourceBranch:
  type: string
  pattern: "^rollback/[a-z0-9_-]+$"
  required: true
  validation: "Must start with 'rollback/'"

targetBranch:
  type: string
  enum: ["main", "staging"]
  required: true
  validation: "Only 'main' and 'staging' allowed"

baseSha:
  type: string
  pattern: "^[a-f0-9]{40}$"
  required: true

changes:
  type: array
  items: FileChange
  required: true
  minItems: 1
  maxItems: 10
  validation: "Must include at least one file change"

title:
  type: string
  required: true
  minLength: 10
  maxLength: 200
  pattern: "^Rollback: .+"
  validation: "Must start with 'Rollback:'"

description:
  type: string
  format: markdown
  required: true
  minLength: 50
  maxLength: 10000

labels:
  type: array
  items: string
  required: true
  mustInclude: ["ai-operator", "rollback"]
  validation: "Must include 'ai-operator' and 'rollback' labels"
```

**Validation:**
- `sourceBranch` must start with `rollback/` prefix
- `targetBranch` restricted to `main` or `staging`
- `title` must start with `Rollback:`
- `labels` must include `ai-operator` and `rollback`
- Max 10 file changes per PR (prevent abuse)

**Rate Limit:** 10 PRs per hour per repository

---

#### getPullRequestStatus

**Permission:** `pull_requests:read`

**Parameters:**
```yaml
repo:
  type: string
  required: true

prNumber:
  type: integer
  required: true
  minimum: 1
```

**Validation:** None (read-only)

**Use Cases:**
- Check CI status before merge
- Verify branch protection requirements

---

#### mergePullRequest

**Permission:** `pull_requests:write` + special conditions

**Parameters:**
```yaml
repo:
  type: string
  required: true

prNumber:
  type: integer
  required: true

mergeMethod:
  type: string
  enum: ["squash"]
  required: false
  default: "squash"
  validation: "Only 'squash' method allowed"
```

**Preconditions (enforced by MCP provider):**
- `ciStatus == "success"` (all CI checks passed)
- `mergeable == true` (no conflicts, branch protection satisfied)
- `approvals >= requiredApprovals` (if production)

**Additional Governance Conditions:**
- ALL 8 invariants must pass (Section 4)
- Environment must be `staging` for auto-merge
- Environment must be `production` → requires human approval (never auto-merge)

**Rate Limit:** 5 merges per hour per repository

---

#### listOpenPullRequests

**Permission:** `pull_requests:read`

**Parameters:**
```yaml
repo:
  type: string
  required: true

targetBranch:
  type: string
  required: false

labels:
  type: array
  items: string
  required: false
```

**Validation:** None (read-only)

**Use Cases:**
- Check for conflicting PRs (invariant I7)
- Prevent duplicate PR creation

---

### 3.2 Kubernetes MCP Calls

#### getDeploymentStatus

**Permission:** `deployments:get` (namespace-scoped)

**Parameters:**
```yaml
namespace:
  type: string
  enum: ["production", "staging"]
  required: true
  validation: "Only 'production' and 'staging' namespaces allowed"

deploymentName:
  type: string
  pattern: "^[a-z0-9-]+$"
  required: true
```

**Validation:**
- `namespace` restricted to allowed namespaces
- `deploymentName` must be alphanumeric with hyphens

**Use Cases:**
- Check replica counts for degradation detection
- Verify health after rollback

---

#### listPods

**Permission:** `pods:list` (namespace-scoped)

**Parameters:**
```yaml
namespace:
  type: string
  enum: ["production", "staging"]
  required: true

labelSelector:
  type: string
  required: false
  pattern: "^[a-z0-9=,]+$"
  maxLength: 200
```

**Validation:**
- `namespace` restricted to allowed namespaces
- `labelSelector` must be valid Kubernetes label selector format

**Use Cases:**
- Debug degradation causes
- Gather pod-level health information

---

### 3.3 Argo CD MCP Calls

#### getApplicationHealth

**Permission:** `applications:get`

**Parameters:**
```yaml
appName:
  type: string
  pattern: "^[a-z0-9-]+$"
  required: true
```

**Validation:**
- `appName` must be alphanumeric with hyphens

**Use Cases:**
- Primary degradation detection
- Health verification after rollback

---

#### getApplicationSyncStatus

**Permission:** `applications:get`

**Parameters:**
```yaml
appName:
  type: string
  pattern: "^[a-z0-9-]+$"
  required: true
```

**Validation:**
- `appName` must be alphanumeric with hyphens

**Use Cases:**
- Get current synced revision
- Verify sync status

---

## 4. Level 2 Autonomy Invariants

**Level 2 Autonomy** allows the AI Operator to auto-merge rollback PRs in **staging environments only**, but ONLY if **all 8 invariants are satisfied**.

**Enforcement Point:** Before calling `github.mergePullRequest`, the `InvariantEnforcement` module must verify all invariants.

### 4.1 Quantitative Invariant Definitions

#### I1: Environment is Staging

**Invariant:** `environment == "staging"`

**Verification Method:**
```python
def check_I1_environment(context: RollbackContext) -> bool:
    # Read from Argo CD application labels
    app = mcp.call("argocd.getApplicationHealth", {"appName": context.appName})
    # Assume Argo CD app has label "environment: staging" or "environment: production"
    environment = get_label(app, "environment")
    return environment == "staging"
```

**Rationale:** Production environments require human oversight; auto-merge is prohibited.

**Failure Action:** If `environment != "staging"`, transition FSM to `AwaitingMergeApproval`, alert human, wait for approval.

---

#### I2: Argo CD Health is Degraded

**Invariant:** `argoHealth == "Degraded"`

**Verification Method:**
```python
def check_I2_health_degraded(context: RollbackContext) -> bool:
    health = mcp.call("argocd.getApplicationHealth", {"appName": context.appName})
    return health.status == "Degraded"
```

**Rationale:** Auto-merge is only justified if application is currently degraded. If health restores before merge, close PR instead.

**Failure Action:** If `argoHealth != "Degraded"`, close PR with comment "Health restored before merge", transition FSM to `Idle`.

---

#### I3: Replica Shortage

**Invariant:** `availableReplicas < desiredReplicas`

**Verification Method:**
```python
def check_I3_replica_shortage(context: RollbackContext) -> bool:
    deployment = mcp.call("kubernetes.getDeploymentStatus", {
        "namespace": context.namespace,
        "deploymentName": context.appName
    })
    return deployment.availableReplicas < deployment.desiredReplicas
```

**Rationale:** Confirms actual replica shortage (not just Argo CD reporting degraded).

**Failure Action:** If `availableReplicas >= desiredReplicas`, abort auto-merge (health may have self-healed).

---

#### I4: Persistent Degradation

**Invariant:** `degradation persists for 3 consecutive 10-second health checks`

**Verification Method:**
```python
def check_I4_persistence(context: RollbackContext) -> bool:
    history = context.healthCheckHistory
    if len(history) < 3:
        return False
    # All 3 checks must show available < desired
    return all(h["available"] < h["desired"] for h in history)
```

**Rationale:** Filters transient issues (e.g., brief pod restarts). Only persistent degradations warrant rollback.

**Failure Action:** If persistence not verified, wait for more health checks (do not auto-merge yet).

---

#### I5: Stable Previous Version

**Invariant:** `rollback target has 99% uptime over 24h when it was deployed` OR `(metrics unavailable AND CI passed)`

**Verification Method:**
```python
def check_I5_stable_previous(context: RollbackContext) -> bool:
    try:
        # Query Prometheus for historical uptime
        uptime = prometheus.query(
            f'avg_over_time(up{{app="{context.appName}", revision="{context.targetRevision}"}}[24h])'
        )
        return uptime >= 0.99  # 99% threshold
    except MetricsUnavailable:
        # Fallback: accept if CI passed
        return context.ciStatusPassed
```

**Rationale:** Ensures rollback target is actually stable (won't cause another degradation).

**Failure Action:** If `uptime < 0.99` and metrics available, abort rollback, alert human "No stable candidate found".

---

#### I6: CI Success

**Invariant:** `rollback PR CI status == "success"`

**Verification Method:**
```python
def check_I6_ci_success(context: RollbackContext) -> bool:
    pr_status = mcp.call("github.getPullRequestStatus", {
        "repo": context.repo,
        "prNumber": context.prNumber
    })
    return pr_status.ciStatus == "success"
```

**Rationale:** Rollback PR must pass all tests (unit, integration, security scans).

**Failure Action:** If `ciStatus != "success"`, abort auto-merge, transition to `Abort`, alert human.

---

#### I7: No Conflicting PRs

**Invariant:** `no other open PRs targeting same branch`

**Verification Method:**
```python
def check_I7_no_conflicts(context: RollbackContext) -> bool:
    open_prs = mcp.call("github.listOpenPullRequests", {
        "repo": context.repo,
        "targetBranch": context.targetBranch,
        "labels": ["ai-operator"]
    })
    # Should be exactly 1 (this rollback PR)
    return len(open_prs.prs) == 1
```

**Rationale:** Prevents race conditions from multiple concurrent rollback attempts.

**Failure Action:** If multiple PRs exist, abort auto-merge, alert human "Conflicting rollback PRs detected".

---

#### I8: Branch Protection Satisfied

**Invariant:** `branch protection rules allow merge`

**Verification Method:**
```python
def check_I8_branch_protection(context: RollbackContext) -> bool:
    pr_status = mcp.call("github.getPullRequestStatus", {
        "repo": context.repo,
        "prNumber": context.prNumber
    })
    return pr_status.mergeable == True
```

**Rationale:** Respects repository branch protection rules (e.g., required reviews, status checks).

**Failure Action:** If `mergeable == false`, abort auto-merge (may indicate conflict or missing required checks).

---

### 4.2 Invariant Enforcement Logic

**Before every auto-merge attempt:**

```python
def enforce_all_invariants(context: RollbackContext) -> Dict[str, str]:
    """
    Returns dict of invariant_id -> "PASS" or "FAIL"
    If any fails, auto-merge is blocked.
    """
    results = {
        "I1_environment": "PASS" if check_I1_environment(context) else "FAIL",
        "I2_health_degraded": "PASS" if check_I2_health_degraded(context) else "FAIL",
        "I3_replica_shortage": "PASS" if check_I3_replica_shortage(context) else "FAIL",
        "I4_persistence": "PASS" if check_I4_persistence(context) else "FAIL",
        "I5_stable_previous": "PASS" if check_I5_stable_previous(context) else "FAIL",
        "I6_ci_success": "PASS" if check_I6_ci_success(context) else "FAIL",
        "I7_no_conflicts": "PASS" if check_I7_no_conflicts(context) else "FAIL",
        "I8_branch_protection": "PASS" if check_I8_branch_protection(context) else "FAIL"
    }

    # Log results
    log_audit({
        "actionType": "INVARIANT_CHECK",
        "correlationId": context.correlationId,
        "invariantResults": results,
        "allPass": all(v == "PASS" for v in results.values())
    })

    return results


def can_auto_merge(context: RollbackContext) -> bool:
    """
    Returns True only if ALL invariants pass.
    """
    results = enforce_all_invariants(context)
    return all(v == "PASS" for v in results.values())
```

**Effect:**
- If `can_auto_merge() == True`: Proceed with `github.mergePullRequest`
- If `can_auto_merge() == False`: Transition FSM to `AwaitingMergeApproval`, alert human, log which invariants failed

---

## 5. Audit Logging Specification

**Requirement:** All AI Operator actions must be logged to an **immutable, append-only audit log**.

### 5.1 Audit Log Format

**Format:** JSON (one log entry per line)

**Storage Backend:** Elasticsearch (index pattern: `ai-operator-audit-{YYYY.MM.DD}`)

**Retention:** 90 days

**Immutability:** Write-once, append-only (no updates or deletions)

**Schema:**

```json
{
  "version": "1.0",
  "timestamp": "2026-02-27T10:35:00.123Z",
  "correlationId": "uuid-1234-5678-90ab-cdef",
  "level": "INFO",
  "actionType": "MCP_CALL",
  "actor": "ai-operator-v1.2.3",
  "mcpCall": {
    "name": "createPullRequest",
    "parameters": {
      "repo": "my-org/payment-service",
      "sourceBranch": "rollback/payment-service-abc123",
      "targetBranch": "staging",
      "title": "Rollback: payment-service to abc123"
    },
    "result": "success",
    "response": {
      "id": 123456789,
      "number": 42,
      "url": "https://github.com/my-org/payment-service/pull/42"
    },
    "durationMs": 234,
    "retryCount": 0
  },
  "reasoning": "Application payment-service in staging degraded: availableReplicas (1) < desiredReplicas (3) for 3 consecutive checks. Creating rollback PR to last stable revision abc123.",
  "invariantsChecked": {
    "I1_environment": "PASS",
    "I2_health_degraded": "PASS",
    "I3_replica_shortage": "PASS",
    "I4_persistence": "PASS",
    "I5_stable_previous": "PASS",
    "I7_no_conflicts": "PASS"
  },
  "fsmState": {
    "before": "CandidateResolved",
    "after": "PRCreated"
  },
  "metadata": {
    "appName": "payment-service",
    "environment": "staging",
    "currentRevision": "def456",
    "targetRevision": "abc123"
  }
}
```

### 5.2 Field Definitions

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `version` | string | Yes | Audit log schema version (default: "1.0") |
| `timestamp` | string (ISO 8601) | Yes | UTC timestamp with millisecond precision |
| `correlationId` | string (UUID) | Yes | Tracks single rollback attempt across all logs |
| `level` | enum | Yes | Log level: DEBUG, INFO, WARN, ERROR |
| `actionType` | enum | Yes | Type of action: MCP_CALL, FSM_TRANSITION, INVARIANT_CHECK, ESCALATION |
| `actor` | string | Yes | AI Operator version identifier |
| `mcpCall` | object | If actionType=MCP_CALL | MCP call details (name, parameters, result, duration) |
| `reasoning` | string | Yes | Human-readable explanation of why action was taken |
| `invariantsChecked` | object | If auto-merge | Results of all invariant checks |
| `fsmState` | object | If actionType=FSM_TRANSITION | Before/after FSM states |
| `metadata` | object | Optional | Additional context (app name, environment, etc.) |

### 5.3 Action Types

| Action Type | When Logged | Key Fields |
|-------------|-------------|------------|
| `MCP_CALL` | Every MCP call | `mcpCall.name`, `mcpCall.parameters`, `mcpCall.result`, `mcpCall.durationMs` |
| `FSM_TRANSITION` | Every state transition | `fsmState.before`, `fsmState.after` |
| `INVARIANT_CHECK` | Before auto-merge | `invariantsChecked` (all 8 invariants) |
| `ESCALATION` | Human alert/escalation | `escalation.type`, `escalation.reason`, `escalation.issueUrl` |
| `ERROR` | Any error/abort | `error.code`, `error.message`, `error.stackTrace` |

### 5.4 Example Log Entries

**MCP Call Success:**
```json
{
  "version": "1.0",
  "timestamp": "2026-02-27T10:35:00.123Z",
  "correlationId": "uuid-1234",
  "level": "INFO",
  "actionType": "MCP_CALL",
  "actor": "ai-operator-v1.2.3",
  "mcpCall": {
    "name": "github.mergePullRequest",
    "parameters": {"repo": "my-org/payment-service", "prNumber": 42},
    "result": "success",
    "response": {"merged": true, "sha": "abc123"},
    "durationMs": 1234,
    "retryCount": 0
  },
  "reasoning": "All invariants passed, auto-merging rollback PR in staging"
}
```

**Invariant Violation:**
```json
{
  "version": "1.0",
  "timestamp": "2026-02-27T10:36:00.456Z",
  "correlationId": "uuid-1234",
  "level": "WARN",
  "actionType": "INVARIANT_CHECK",
  "actor": "ai-operator-v1.2.3",
  "reasoning": "Invariant I6_ci_success failed, blocking auto-merge",
  "invariantsChecked": {
    "I1_environment": "PASS",
    "I2_health_degraded": "PASS",
    "I3_replica_shortage": "PASS",
    "I4_persistence": "PASS",
    "I5_stable_previous": "PASS",
    "I6_ci_success": "FAIL",
    "I7_no_conflicts": "PASS",
    "I8_branch_protection": "PASS"
  },
  "fsmState": {"before": "AwaitingMergeApproval", "after": "Abort"}
}
```

---

## 6. Error Handling and Retry Logic

### 6.1 MCP Error Handling

**General Policy:**
- **Retryable errors:** Retry up to 3 times with exponential backoff
- **Non-retryable errors:** Abort immediately, alert human

**Backoff Schedule:**
1. First retry: 1s delay
2. Second retry: 2s delay
3. Third retry: 4s delay
4. After 3 failures: Abort and escalate

### 6.2 Error Recovery by MCP Call

#### createPullRequest

| Error Code | Retryable | Recovery Action |
|------------|-----------|----------------|
| `BRANCH_NOT_FOUND` | No | Abort rollback, alert human "Source branch creation failed" |
| `PERMISSION_DENIED` | No | **Escalate to admin**, abort rollback, log security alert |
| `CONFLICT` | Partial | Retry once with rebase; if still fails, abort rollback |
| `NETWORK_ERROR` | Yes | Retry 3 times with exponential backoff |
| `RATE_LIMIT_EXCEEDED` | Yes | Wait for rate limit reset, retry once |
| `VALIDATION_ERROR` | No | Abort, log error (indicates bug in PR generation logic) |

**Implementation:**
```python
def create_pr_with_retry(params: dict) -> dict:
    for attempt in range(1, 4):
        try:
            return mcp.call("github.createPullRequest", params)
        except MCPError as e:
            if e.code == "PERMISSION_DENIED":
                escalate_to_admin("GitHub permissions missing")
                raise
            elif e.code == "CONFLICT":
                if attempt == 1:
                    # Try rebase once
                    params = rebase_changes(params)
                    continue
                else:
                    abort_rollback("Unresolvable conflict")
                    raise
            elif e.code in ["NETWORK_ERROR", "RATE_LIMIT_EXCEEDED"]:
                if attempt < 3:
                    time.sleep(2 ** attempt)  # Exponential backoff
                    continue
                else:
                    abort_rollback("GitHub API unreachable after 3 retries")
                    raise
            else:
                # Non-retryable
                abort_rollback(f"createPullRequest failed: {e.code}")
                raise
```

---

#### mergePullRequest

| Error Code | Retryable | Recovery Action |
|------------|-----------|----------------|
| `NOT_MERGEABLE` | No | Abort auto-merge, alert human "PR not mergeable (conflicts or branch protection)" |
| `PERMISSION_DENIED` | No | **Escalate to admin**, abort rollback |
| `CI_PENDING` | Special | Wait and poll for up to 300s; if still pending, abort |
| `ALREADY_MERGED` | No | Treat as success, continue to health monitoring |
| `NETWORK_ERROR` | Yes | Retry 3 times with exponential backoff |

**Implementation:**
```python
def merge_pr_with_retry(pr_number: int) -> dict:
    for attempt in range(1, 4):
        try:
            return mcp.call("github.mergePullRequest", {"prNumber": pr_number})
        except MCPError as e:
            if e.code == "CI_PENDING":
                # Wait for CI (handled separately by wait_for_ci function)
                raise
            elif e.code == "ALREADY_MERGED":
                log.info("PR already merged, continuing")
                return {"merged": True}
            elif e.code == "NOT_MERGEABLE":
                abort_auto_merge("PR not mergeable")
                raise
            elif e.code == "NETWORK_ERROR" and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            else:
                abort_rollback(f"mergePullRequest failed: {e.code}")
                raise
```

---

#### getApplicationHealth

| Error Code | Retryable | Recovery Action |
|------------|-----------|----------------|
| `NOT_FOUND` | No | Abort operation, verify app name, alert human |
| `PERMISSION_DENIED` | No | **Escalate to admin**, check RBAC |
| `NETWORK_ERROR` | Yes | Retry 3 times; if still failing, skip this health check (log warning) |

**Fallback:** If Argo CD API unavailable after retries, fall back to Kubernetes-only health checks.

---

### 6.3 Timeout Handling

| Operation | Timeout | Action on Timeout |
|-----------|---------|-------------------|
| Candidate search | 120s | Abort rollback, alert "No candidate found in time" |
| CI wait | 300s | Abort rollback, alert "CI timed out" |
| Merge approval (production) | 3600s | Abort rollback, alert "No approval received" |
| Health restoration (post-merge) | 600s | Abort rollback, alert "Health did not restore" |

---

## 7. Human Escalation Procedure

**Trigger Conditions:**
- Any invariant violation during auto-merge attempt
- MCP call failure after retries (PERMISSION_DENIED, NOT_MERGEABLE, etc.)
- Rollback PR created but degradation resolves before merge (health restored)
- Rollback itself causes new degradation
- FSM transitions to `Abort` state
- Timeout exceeded (CI, approval, health restoration)

### 7.1 Escalation Actions

**Step 1: Create GitHub Issue**

**Repository:** `ai-operator-alerts` (dedicated repo for operator alerts)

**Title Format:** `[URGENT] AI Operator Escalation: {appName} - {reason}`

**Body Template:**
```markdown
## 🚨 AI Operator Escalation

**Correlation ID:** {correlationId}
**Application:** {appName}
**Environment:** {environment}
**Timestamp:** {timestamp}
**FSM State:** {currentFsmState}

---

### Failure Reason

{detailedReason}

---

### Context

- **Current Revision:** {currentRevision}
- **Target Revision:** {targetRevision}
- **Argo CD Health:** {argoHealth}
- **Replica Status:** {availableReplicas}/{desiredReplicas}

---

### Invariant Check Results

{invariantResultsTable}

---

### Recommended Action

{recommendedAction}

---

### Audit Trail

View full audit logs in Elasticsearch:
- Index: `ai-operator-audit-{date}`
- Query: `correlationId: "{correlationId}"`

---

### Commands

To resume or abort this rollback attempt:
- **Approve:** Comment `APPROVE` on this issue
- **Abort:** Comment `ABORT` on this issue
- **Manual Takeover:** Comment `MANUAL` on this issue
```

**Labels:** `["ai-operator", "escalation", "urgent", environment]`

**Assignees:** `@oncall` (GitHub team)

---

**Step 2: Send Slack Alert**

**Channel:** `#ai-operator-alerts`

**Message Format:**
```
🚨 @oncall AI Operator Escalation

**App:** {appName} ({environment})
**Reason:** {shortReason}
**Correlation ID:** {correlationId}

**Action Required:** Review and respond to GitHub issue:
{issueUrl}

**Quick Actions:**
- ✅ Approve auto-merge: Comment `APPROVE`
- ❌ Abort rollback: Comment `ABORT`
- 👤 Manual takeover: Comment `MANUAL`
```

**Priority:** High (triggers push notification)

---

**Step 3: Transition FSM to AwaitingHumanReview**

**New FSM State:** `AwaitingHumanReview` (pauses all automated actions)

**Polling:** Check GitHub issue for commands every 30 seconds

**Supported Commands:**
- `APPROVE` → Resume auto-merge (if invariants now pass)
- `ABORT` → Transition to `Abort`, close PR
- `MANUAL` → Close AI-created PR, human takes over manually

---

**Step 4: Timeout Escalation**

| Time Elapsed | Action |
|--------------|--------|
| 0 min | GitHub issue + Slack alert |
| 60 min (no response) | Send page to oncall engineer (PagerDuty/Opsgenie) |
| 240 min (no response) | Transition to `Abort`, alert manager, close PR |

---

### 7.2 Example Escalation Scenarios

**Scenario 1: Invariant I6_ci_success Failed**

```
Trigger: CI status == "failure" on rollback PR

Actions:
1. Create GitHub issue: "[URGENT] AI Operator Escalation: payment-service - CI Failure"
2. Send Slack alert
3. Transition FSM to AwaitingHumanReview
4. Log audit entry: actionType=ESCALATION, reason=CI_FAILURE

Recommended Action (in issue):
"CI failed on rollback PR #42. Review test failures, fix if needed, or manually merge if safe to override."
```

**Scenario 2: MCP Call PERMISSION_DENIED**

```
Trigger: github.createPullRequest fails with PERMISSION_DENIED

Actions:
1. Create GitHub issue: "[URGENT] AI Operator Escalation: payment-service - Permission Denied"
2. Send Slack alert
3. Transition FSM to Abort
4. Log audit entry: actionType=ESCALATION, reason=PERMISSION_DENIED
5. **Alert admin team** (separate alert for security issue)

Recommended Action (in issue):
"AI Operator lacks 'pull_requests:write' permission on my-org/payment-service. Check service account GitHub App installation and permissions."
```

---

## 8. RBAC Model

### 8.1 Service Account Definition

**Name:** `ai-operator`

**Type:** Kubernetes ServiceAccount + GitHub App

**Scope:** Namespace-scoped (Kubernetes), Repository-scoped (GitHub)

### 8.2 GitHub Permissions

**GitHub App:** `ai-devops-operator-app`

**Repository Access:** Organization-scoped (`my-org/*`)

**Permissions:**

| Permission Scope | Access Level | Purpose |
|------------------|--------------|---------|
| `contents` | `read` | Read repository files, Git history |
| `pull_requests` | `write` | Create, update, comment on PRs |
| `pull_requests` | `read` | Get PR status, list open PRs |
| `checks` | `read` | Read CI status |
| `metadata` | `read` | Read repository metadata |

**Restrictions:**
- **Cannot** push directly to any branch
- **Cannot** force-push or delete branches
- **Cannot** modify branch protection rules
- **Cannot** modify repository settings, webhooks, or secrets
- **Cannot** create/delete tags or releases
- **Cannot** approve own PRs (requires human approval)

### 8.3 Kubernetes RBAC

**ServiceAccount:** `ai-operator` (namespace: `ai-operator-system`)

**ClusterRole:** `ai-operator-reader`

**Permissions:**

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: ai-operator-reader
rules:
  # Deployments (read-only)
  - apiGroups: ["apps"]
    resources: ["deployments"]
    verbs: ["get", "list"]
    resourceNames: []  # All deployments in allowed namespaces

  # Pods (read-only)
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list"]

  # Namespaces (read metadata only)
  - apiGroups: [""]
    resources: ["namespaces"]
    verbs: ["get"]

  # No write operations allowed
  # No delete operations allowed
  # No access to secrets, configmaps
```

**ClusterRoleBinding:** Binds `ai-operator` ServiceAccount to `ai-operator-reader` ClusterRole

**Namespace Restrictions:** Only `production` and `staging` namespaces (enforced by admission controller)

### 8.4 Argo CD RBAC

**User:** `ai-operator`

**Role:** `ai-operator-readonly`

**Permissions:**

```yaml
policy:
  - p, role:ai-operator-readonly, applications, get, *, allow
  - p, role:ai-operator-readonly, applications, health, *, allow
  - p, role:ai-operator-readonly, applications, sync-status, *, allow

  # No sync operations allowed
  # No delete operations allowed
  # No update operations allowed

roleBindings:
  - user: ai-operator
    role: ai-operator-readonly
```

---

## 9. Rate Limiting and Quotas

### 9.1 GitHub API Rate Limits

**Authenticated requests:** 5000 requests/hour (GitHub limit)

**AI Operator expected usage:** ~100 requests/hour

**Rate limit monitoring:**
- Track `X-RateLimit-Remaining` header
- Alert if remaining < 1000
- Pause operations if remaining < 100

**Per-operation limits (AI Operator enforced):**
- `createPullRequest`: Max 10 PRs/hour per repository
- `mergePullRequest`: Max 5 merges/hour per repository
- `listOpenPullRequests`: Max 100 calls/hour

### 9.2 Kubernetes API Rate Limits

**Cluster-dependent** (no hard GitHub-style limits)

**Best practice:** Limit to 1 request per resource per 10 seconds

**AI Operator compliance:**
- `getDeploymentStatus`: Called every 10s per monitored app
- `listPods`: Called only during degradation investigation (infrequent)

### 9.3 Argo CD API Rate Limits

**No hard limits** (server-dependent)

**Best practice:** Limit to 1 health check per app per 10 seconds

**AI Operator compliance:**
- `getApplicationHealth`: Called every 10s per monitored app
- `getApplicationSyncStatus`: Called once per rollback attempt

### 9.4 Operational Quotas

| Quota | Limit | Enforcement |
|-------|-------|-------------|
| Max concurrent rollback attempts | 10 | FSM controller |
| Max rollback attempts per app per day | 5 | Abort after 5, alert admin |
| Max PR age before cleanup | 7 days | Auto-close stale rollback PRs |
| Max audit log entries per day | 100,000 | Alert if exceeded (indicates issue) |

---

## 10. Compliance and Security

### 10.1 Security Principles

| Principle | Implementation |
|-----------|---------------|
| **Least Privilege** | Read-only Kubernetes access, no direct cluster modifications |
| **Separation of Duties** | AI proposes (creates PRs), humans approve (production merges) |
| **Audit Trail** | Immutable logs in Elasticsearch, 90-day retention |
| **Defense in Depth** | Multiple invariant checks, FSM guards, MCP validation |
| **Fail-Safe Defaults** | Any error → abort and alert, never auto-merge on uncertainty |

### 10.2 Compliance Requirements

**SOC 2 Compliance:**
- ✅ All changes traceable through PRs
- ✅ Immutable audit logs
- ✅ Human approval for production changes
- ✅ Automated security scanning (CI checks)

**Change Management:**
- ✅ All production changes require documented approval (PR approval)
- ✅ Emergency rollbacks documented in audit log
- ✅ All actions reversible (Git revert)

**Data Privacy:**
- ✅ No PII in logs (only application names, correlation IDs)
- ✅ Secrets never logged (validated by audit log schema)

### 10.3 Security Monitoring

**Alerts:**
- Any `PERMISSION_DENIED` error → immediate security alert
- Rate limit approaching → warning
- Unusual activity (>10 rollbacks/hour) → investigation
- Audit log write failures → critical alert

**Regular Audits:**
- Weekly review of all escalations
- Monthly review of invariant violations
- Quarterly review of RBAC permissions

---

## Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | (Original) | - | Initial system prompt |
| 2.0 | 2026-02-27 | AI DevOps Team | Complete governance framework: invariants, audit logs, error handling, RBAC |

---

**Related Documents:**
- [Architecture](./ai-operator-architecture.md) - System overview
- [MCP API Specification](./ai-operator-mcp-api-spec.md) - MCP call details
- [Rollback Engine](./ai-rollback-engine-spec.md) - FSM implementation
- [Full Context](./ai-operator-full-context.md) - Operational rules
