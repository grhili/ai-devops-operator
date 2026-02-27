# AI Rollback Engine - Technical Specification

**Version:** 2.0
**Last Updated:** 2026-02-27
**Status:** Production-Ready Implementation Blueprint
**Audience:** Implementation Engineers, QA, SREs

---

## Table of Contents

1. [Purpose](#1-purpose)
2. [Architectural Overview](#2-architectural-overview)
3. [Finite State Machine Design](#3-finite-state-machine-design)
4. [Module Specifications](#4-module-specifications)
5. [Event Schemas](#5-event-schemas)
6. [Rollback Candidate Selection Algorithm](#6-rollback-candidate-selection-algorithm)
7. [Module Communication Architecture](#7-module-communication-architecture)
8. [Timing Configuration](#8-timing-configuration)
9. [State Persistence and Recovery](#9-state-persistence-and-recovery)
10. [Observability & Metrics](#10-observability--metrics)
11. [Testing Strategy](#11-testing-strategy)
12. [Sequence Diagrams](#12-sequence-diagrams)

---

## 1. Purpose

This document defines the **technical implementation** of the AI Rollback Engine, including:
- Complete finite state machine (FSM) with guards, triggers, and actions
- Event-driven module architecture with detailed communication protocols
- Rollback candidate selection algorithm
- State persistence and crash recovery procedures
- Environment-aware execution paths (staging vs production)

**Prerequisites:** Read [`ai-operator-architecture.md`](./ai-operator-architecture.md) and [`ai-operator-mcp-api-spec.md`](./ai-operator-mcp-api-spec.md) first.

---

## 2. Architectural Overview

### 2.1 High-Level Architecture

The Rollback Engine is composed of **7 independent modules** coordinated by a **central FSM controller**:

```
┌─────────────────────────────────────────────────────────────────┐
│                     FSM Controller                               │
│                  (State Machine Orchestrator)                    │
└───────────┬─────────────────────────────────────────────────────┘
            │
            │ subscribes to all events, dispatches state transitions
            │
            ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Event Bus (NATS)                              │
│  Topics: degradation.*, candidate.*, pr.*, merge.*, health.*    │
└─────────┬─────────┬──────────┬──────────┬──────────┬───────────┘
          │         │          │          │          │
          ▼         ▼          ▼          ▼          ▼
    ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐
    │Degradat-│ │Stability│ │Rollback │ │   PR    │ │  Merge  │
    │ion      │ │Analyzer │ │Candidate│ │Generator│ │Controlle│
    │Detector │ │         │ │Resolver │ │         │ │r        │
    └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘
          │                                                │
          └────────────────┐                   ┌──────────┘
                           ▼                   ▼
                    ┌─────────────────┐ ┌─────────────────┐
                    │ Invariant       │ │  Audit &        │
                    │ Enforcement     │ │  Logging        │
                    └─────────────────┘ └─────────────────┘
                           │
                           ▼
                    ┌─────────────────┐
                    │  State Store    │
                    │  (etcd)         │
                    └─────────────────┘
```

### 2.2 Module Summary

| Module | Responsibility | Input | Output |
|--------|---------------|-------|--------|
| **DegradationDetector** | Polls Argo CD/K8s every 10s | Argo CD health API | `degradation.detected` event |
| **StabilityAnalyzer** | Verifies 3 consecutive degraded checks | `degradation.detected` | `degradation.confirmed` event |
| **RollbackCandidateResolver** | Finds last stable Git revision | `degradation.confirmed` | `candidate.resolved` event |
| **PRGenerator** | Creates rollback PR on GitHub | `candidate.resolved` | `pr.created` event |
| **MergeController** | Waits for CI, checks invariants, merges | `pr.created` | `merge.completed` event |
| **InvariantEnforcement** | Validates all safety invariants | Any state transition | Approval/rejection |
| **Audit & Logging** | Records all actions with reasoning | All events | Elasticsearch logs |

### 2.3 Data Flow

**Happy Path (Staging):**
1. DegradationDetector polls → detects `Degraded` → publishes `degradation.detected`
2. StabilityAnalyzer waits 3 checks → publishes `degradation.confirmed`
3. RollbackCandidateResolver queries Git+metrics → publishes `candidate.resolved`
4. PRGenerator creates PR → publishes `pr.created`
5. MergeController waits for CI → checks invariants → auto-merges → publishes `merge.completed`
6. HealthMonitor validates recovery → FSM transitions to `RollbackComplete`

**Production Path:**
Steps 1-4 identical, then:
5. MergeController waits for CI → checks invariants → **alerts human** → waits for approval
6. Human approves → MergeController merges → publishes `merge.completed`
7. HealthMonitor validates recovery → FSM transitions to `RollbackComplete`

---

## 3. Finite State Machine Design

### 3.1 Complete State Transition Table

| From State | Event | Guard Conditions | Actions | To State |
|------------|-------|------------------|---------|----------|
| **Idle** | `DegradationDetected` | `env == 'staging' && persistent && no_conflicts` | `log_event`, `start_timer`, `create_correlation_id` | **DegradationDetected** |
| **Idle** | `DegradationDetected` | `env == 'production' && persistent` | `log_event`, `alert_human`, `create_correlation_id` | **DegradationDetected** |
| **Idle** | `DegradationDetected` | `!persistent` (transient issue) | `log_event`, `ignore` | **Idle** |
| **DegradationDetected** | `DegradationConfirmed` | `still_degraded` | `log_confirmation` | **DegradationDetected** (waiting for candidate) |
| **DegradationDetected** | `CandidateFound` | `CI_passed && is_older_version && stable_history` | `log_candidate`, `save_target_sha` | **CandidateResolved** |
| **DegradationDetected** | `NoCandidateFound` | `timeout_exceeded \|\| all_candidates_failed` | `log_error`, `alert_human` | **Abort** |
| **DegradationDetected** | `HealthRestored` | `health == 'Healthy' for 60s` | `log_recovery`, `cleanup` | **Idle** |
| **CandidateResolved** | `PRCreatedEvent` | `pr_valid && pr_url_exists` | `log_pr_url`, `save_pr_number` | **PRCreated** |
| **CandidateResolved** | `PRCreationFailed` | `github_error \|\| validation_failed` | `log_error`, `alert_human` | **Abort** |
| **PRCreated** | `CISuccess` | `env == 'staging' && all_invariants_pass` | `log_ci_success`, `check_auto_merge_conditions` | **AwaitingMergeApproval** |
| **PRCreated** | `CISuccess` | `env == 'production'` | `log_ci_success`, `alert_human`, `request_approval` | **AwaitingMergeApproval** |
| **PRCreated** | `CIFailure` | `ci_status == 'failure'` | `log_ci_failure`, `alert_human` | **Abort** |
| **PRCreated** | `CITimeout` | `wait_time > CI_WAIT_TIMEOUT` | `log_timeout`, `alert_human` | **Abort** |
| **AwaitingMergeApproval** | `AutoMergeConditionsMet` | `env == 'staging' && still_degraded && all_invariants_pass` | `merge_pr`, `log_merge` | **RollbackMerged** |
| **AwaitingMergeApproval** | `HumanApproved` | `approvals >= required_approvals` | `merge_pr`, `log_merge` | **RollbackMerged** |
| **AwaitingMergeApproval** | `InvariantViolation` | `any_invariant_failed` | `log_violation`, `alert_human` | **Abort** |
| **AwaitingMergeApproval** | `ApprovalTimeout` | `env == 'production' && wait_time > MERGE_APPROVAL_TIMEOUT` | `log_timeout`, `alert_human` | **Abort** |
| **AwaitingMergeApproval** | `HealthRestored` | `health == 'Healthy' for 60s` | `log_recovery`, `close_pr`, `cleanup` | **Idle** |
| **RollbackMerged** | `HealthRestored` | `health == 'Healthy' for 60s` | `log_success`, `close_pr`, `update_metrics`, `cleanup` | **RollbackComplete** |
| **RollbackMerged** | `StillDegraded` | `timeout_exceeded && still_degraded` | `log_failure`, `alert_human` | **Abort** |
| **RollbackMerged** | `NewDegradation` | `health == 'Degraded' after rollback` | `log_rollback_failure`, `alert_human` | **Abort** |
| **RollbackComplete** | - | - | `archive_state`, `emit_metrics` | **Idle** (after 60s) |
| **Abort** | `HumanReset` | `manual_reset_command` | `reset_state`, `cleanup` | **Idle** |
| **Abort** | - | `age > 24h` | `archive_state`, `emit_alert` | **Abort** (terminal) |
| **Any State** | `InvariantViolation` | `critical_invariant_failed` | `log_violation`, `alert_human`, `emergency_stop` | **Abort** |

### 3.2 State Descriptions

#### Idle
- **Meaning:** No active rollback attempts, system monitoring health
- **Entry Actions:** None
- **Exit Actions:** Generate correlation ID for new rollback attempt
- **Terminal:** No

#### DegradationDetected
- **Meaning:** Application degraded, verifying persistence and finding candidate
- **Entry Actions:** Start degradation timer, log initial detection
- **Exit Actions:** Stop degradation timer
- **Terminal:** No
- **Max Duration:** 120s (CANDIDATE_SEARCH_TIMEOUT)

#### CandidateResolved
- **Meaning:** Valid rollback target identified, ready to create PR
- **Entry Actions:** Log candidate SHA and metadata
- **Exit Actions:** None
- **Terminal:** No

#### PRCreated
- **Meaning:** Rollback PR created on GitHub, waiting for CI
- **Entry Actions:** Log PR URL and number, start CI timer
- **Exit Actions:** Stop CI timer
- **Terminal:** No
- **Max Duration:** 300s (CI_WAIT_TIMEOUT)

#### AwaitingMergeApproval
- **Meaning:** CI passed, checking invariants and waiting for merge decision
- **Entry Actions:** Evaluate all invariants, log results
- **Exit Actions:** None
- **Terminal:** No
- **Max Duration:** 3600s (production) or immediate (staging if auto-merge)

#### RollbackMerged
- **Meaning:** PR merged, monitoring for health restoration
- **Entry Actions:** Start health monitoring timer
- **Exit Actions:** Stop health monitoring
- **Terminal:** No
- **Max Duration:** 600s (10 minutes to recover)

#### RollbackComplete
- **Meaning:** Health restored successfully, rollback succeeded
- **Entry Actions:** Log success metrics, archive correlation ID
- **Exit Actions:** Clean up state from etcd
- **Terminal:** Yes

#### Abort
- **Meaning:** Unrecoverable error or invariant violation, human intervention required
- **Entry Actions:** Log abort reason, create GitHub issue, send Slack alert
- **Exit Actions:** Mark state as aborted in etcd (retain for 7 days)
- **Terminal:** Yes (can be reset by human)

### 3.3 Guard Condition Definitions

| Guard | Definition | Verification Method |
|-------|-----------|---------------------|
| `persistent` | Degraded for 3 consecutive 10s checks | `len(healthCheckHistory) >= 3 && all(h.available < h.desired for h in healthCheckHistory)` |
| `no_conflicts` | No other open rollback PRs on target branch | `listOpenPullRequests(repo, targetBranch, labels=['ai-operator']).length == 0` |
| `still_degraded` | Currently degraded | `getApplicationHealth(appName).status == 'Degraded'` |
| `CI_passed` | All CI checks successful | `getPullRequestStatus(prId).ciStatus == 'success'` |
| `is_older_version` | Candidate SHA is ancestor of current | Git ancestry check |
| `stable_history` | 99% uptime when deployed | Prometheus query OR (metrics unavailable AND CI passed) |
| `all_invariants_pass` | All 8 invariants true | See `ai-operator-governance-spec.md` section 4.1 |
| `env == 'staging'` | Target environment is staging | Read from Argo CD app labels |
| `approvals >= required_approvals` | Sufficient human reviews | `getPullRequestStatus(prId).approvals >= .requiredApprovals` |
| `timeout_exceeded` | Operation exceeded max duration | `current_time - start_time > TIMEOUT` |

---

## 4. Module Specifications

### 4.1 DegradationDetector

**Purpose:** Continuously monitors Argo CD applications for health degradation.

**Polling Frequency:** Every 10 seconds per application

**MCP Calls Used:**
- `argocd.getApplicationHealth(appName)` → Get overall health
- `kubernetes.getDeploymentStatus(namespace, deploymentName)` → Get replica counts

**Logic:**
```python
def poll_application(app_name: str) -> Optional[DegradationEvent]:
    health = mcp.call("argocd.getApplicationHealth", {"appName": app_name})

    if health.status != "Degraded":
        return None  # Healthy, no action

    # Get deployment details for replica count
    deployment = mcp.call("kubernetes.getDeploymentStatus", {
        "namespace": get_namespace(app_name),
        "deploymentName": app_name
    })

    if deployment.availableReplicas < deployment.desiredReplicas:
        return DegradationEvent(
            appName=app_name,
            argoHealth=health.status,
            desiredReplicas=deployment.desiredReplicas,
            availableReplicas=deployment.availableReplicas,
            timestamp=now()
        )

    return None
```

**Output Event:** `degradation.detected` (see Section 5.1)

**Error Handling:**
- If `getApplicationHealth` fails 3 times: alert human, skip app for 5 minutes
- If `getDeploymentStatus` fails: log warning, use Argo health only

---

### 4.2 StabilityAnalyzer

**Purpose:** Filters transient degradations by requiring 3 consecutive failed checks.

**Input Event:** `degradation.detected`

**State Management:** Maintains sliding window of last 3 health checks per app in memory cache

**Logic:**
```python
def analyze_degradation(event: DegradationEvent) -> Optional[DegradationConfirmedEvent]:
    # Add to history
    history = get_health_history(event.appName)
    history.append({
        "timestamp": event.timestamp,
        "available": event.availableReplicas,
        "desired": event.desiredReplicas
    })

    # Keep only last 3
    history = history[-3:]

    # Check persistence
    if len(history) == 3 and all(h["available"] < h["desired"] for h in history):
        return DegradationConfirmedEvent(
            appName=event.appName,
            environment=get_environment(event.appName),
            healthCheckHistory=history,
            correlationId=generate_uuid()
        )

    return None  # Not yet persistent
```

**Output Event:** `degradation.confirmed` (see Section 5.2)

---

### 4.3 RollbackCandidateResolver

**Purpose:** Identifies the last known-good Git revision for rollback.

**Input Event:** `degradation.confirmed`

**MCP Calls Used:**
- `github.readRepositoryContent(repo, ".argocd/application.yaml")` → Get current revision
- `argocd.getApplicationSyncStatus(appName)` → Get current synced revision
- GitHub API (via MCP) to list commits
- Prometheus HTTP API for historical uptime metrics

**Algorithm:** See Section 6 for detailed algorithm

**Output Event:** `candidate.resolved` (see Section 5.3)

**Timeout:** 120 seconds (CANDIDATE_SEARCH_TIMEOUT)

---

### 4.4 PRGenerator

**Purpose:** Creates rollback PR on GitHub with detailed context.

**Input Event:** `candidate.resolved`

**MCP Calls Used:**
- `github.listOpenPullRequests(repo, targetBranch, labels=["ai-operator"])` → Check for duplicates
- `github.readRepositoryContent(repo, ".argocd/application.yaml", ref="main")` → Get current content
- `github.createPullRequest(...)` → Create PR

**PR Template:**
```markdown
## 🤖 Automated Rollback

**Triggered by:** AI DevOps Operator
**Correlation ID:** {correlationId}
**Environment:** {environment}
**Timestamp:** {timestamp}

---

### Degradation Details

- **Application:** {appName}
- **Current Revision:** {currentRevision} (deployed {timeAgo})
- **Argo CD Health:** {argoHealth}
- **Replica Status:** {availableReplicas}/{desiredReplicas} available
- **Persistence:** Degraded for 3 consecutive health checks (30 seconds)

### Rollback Target

- **Target Revision:** {targetRevision}
- **Commit Message:** {commitMessage}
- **Commit Author:** {commitAuthor}
- **Commit Date:** {commitDate}
- **Stability Score:** {uptimePercentage}% uptime over 24h when deployed

### Changes

```diff
- targetRevision: {currentRevision}
+ targetRevision: {targetRevision}
```

### CI Status

CI checks must pass before merge. Required checks:
- [ ] Unit tests
- [ ] Integration tests
- [ ] Security scan

### Merge Strategy

**{environment == 'staging' ? 'AUTO-MERGE ENABLED' : 'MANUAL APPROVAL REQUIRED'}**

{if staging}
This PR will be **automatically merged** after CI passes if all safety invariants are satisfied.
{else}
This PR requires **1 senior maintainer approval** before merge.
{endif}

---

**Audit Log:** See Elasticsearch index `ai-operator-audit-{YYYY.MM.DD}` for full decision trail.
```

**Output Event:** `pr.created` (see Section 5.4)

**Error Handling:**
- If `createPullRequest` fails with `CONFLICT`: Abort, alert human
- If `createPullRequest` fails with `PERMISSION_DENIED`: Abort, escalate to admin
- If duplicate PR exists: Reuse existing PR, update description

---

### 4.5 MergeController

**Purpose:** Waits for CI, enforces invariants, and merges PR (staging) or alerts human (production).

**Input Event:** `pr.created`

**MCP Calls Used:**
- `github.getPullRequestStatus(repo, prNumber)` → Poll CI status every 5s
- `github.mergePullRequest(repo, prNumber, mergeMethod="squash")` → Merge after checks
- `kubernetes.getDeploymentStatus(...)` → Re-verify degradation before merge
- `argocd.getApplicationHealth(...)` → Re-verify health

**Logic Flow:**

```python
def handle_pr_created(event: PRCreatedEvent):
    # 1. Wait for CI
    ci_status = wait_for_ci(event.prNumber, timeout=CI_WAIT_TIMEOUT)
    if ci_status != "success":
        publish("merge.ci_failed", ...)
        return

    # 2. Check invariants
    invariants = check_all_invariants(event)
    if not all(invariants.values()):
        publish("merge.invariant_violation", ...)
        return

    # 3. Environment-specific path
    if event.environment == "staging":
        # Re-check degradation (might have self-healed)
        if still_degraded(event.appName):
            merge_pr(event.prNumber)
            publish("merge.completed", ...)
        else:
            close_pr(event.prNumber, reason="Health restored before merge")
            publish("merge.skipped", ...)
    else:  # production
        # Alert human, wait for approval
        alert_human(event)
        wait_for_approval(event.prNumber, timeout=MERGE_APPROVAL_TIMEOUT)
        merge_pr(event.prNumber)
        publish("merge.completed", ...)
```

**Invariant Checks:** Calls `InvariantEnforcement` module (see Section 4.6)

**Output Events:** `merge.completed`, `merge.ci_failed`, `merge.invariant_violation`, `merge.skipped`

---

### 4.6 InvariantEnforcement

**Purpose:** Validates all 8 safety invariants before auto-merge.

**Input:** Any request to auto-merge

**Invariants Checked:** See [`ai-operator-governance-spec.md`](./ai-operator-governance-spec.md) Section 4.1

**Returns:**
```python
{
    "I1_environment": "PASS",
    "I2_health_degraded": "PASS",
    "I3_replica_shortage": "PASS",
    "I4_persistence": "PASS",
    "I5_stable_previous": "PASS",
    "I6_ci_success": "PASS",
    "I7_no_conflicts": "PASS",
    "I8_branch_protection": "PASS"
}
```

**Logic:**
```python
def check_all_invariants(context: RollbackContext) -> Dict[str, str]:
    results = {}

    # I1: Environment is staging
    results["I1_environment"] = "PASS" if context.environment == "staging" else "FAIL"

    # I2: Argo CD health is Degraded
    health = mcp.call("argocd.getApplicationHealth", {"appName": context.appName})
    results["I2_health_degraded"] = "PASS" if health.status == "Degraded" else "FAIL"

    # I3: Available replicas < desired
    deployment = mcp.call("kubernetes.getDeploymentStatus", {...})
    results["I3_replica_shortage"] = "PASS" if deployment.availableReplicas < deployment.desiredReplicas else "FAIL"

    # I4: Persistence (3 checks)
    results["I4_persistence"] = "PASS" if len(context.healthCheckHistory) >= 3 else "FAIL"

    # I5: Stable previous version
    results["I5_stable_previous"] = "PASS" if context.targetUptimePercent >= 99.0 else "FAIL"

    # I6: CI success
    pr_status = mcp.call("github.getPullRequestStatus", {"prNumber": context.prNumber})
    results["I6_ci_success"] = "PASS" if pr_status.ciStatus == "success" else "FAIL"

    # I7: No conflicts (no other open PRs)
    open_prs = mcp.call("github.listOpenPullRequests", {
        "repo": context.repo,
        "targetBranch": context.targetBranch,
        "labels": ["ai-operator"]
    })
    results["I7_no_conflicts"] = "PASS" if len(open_prs.prs) == 1 else "FAIL"

    # I8: Branch protection allows merge
    results["I8_branch_protection"] = "PASS" if pr_status.mergeable else "FAIL"

    return results
```

**Effect:** If any invariant fails, FSM transitions to `AwaitingMergeApproval` (no auto-merge).

---

### 4.7 Audit & Logging

**Purpose:** Records all actions, MCP calls, and FSM transitions to immutable audit log.

**Log Format:** See [`ai-operator-governance-spec.md`](./ai-operator-governance-spec.md) Section 4.2

**Storage:** Elasticsearch with index pattern `ai-operator-audit-{YYYY.MM.DD}`

**Retention:** 90 days

**Key Events Logged:**
- Every FSM state transition (with before/after states)
- Every MCP call (with parameters, response, duration)
- Every invariant check (with results)
- Every human escalation
- Every error and abort

---

## 5. Event Schemas

All events use JSON format and include standard fields:
- `type`: Event type (e.g., "DegradationDetected")
- `timestamp`: ISO 8601 timestamp
- `correlationId`: UUID tracking the rollback attempt
- `version`: Event schema version (default "1.0")

### 5.1 DegradationDetected Event

```json
{
  "type": "DegradationDetected",
  "version": "1.0",
  "timestamp": "2026-02-27T10:30:00Z",
  "correlationId": "uuid-1234",
  "appName": "payment-service",
  "namespace": "production",
  "environment": "production",
  "argoHealth": "Degraded",
  "argoMessage": "Deployment has insufficient replicas",
  "desiredReplicas": 3,
  "availableReplicas": 1,
  "readyReplicas": 1
}
```

### 5.2 DegradationConfirmed Event

```json
{
  "type": "DegradationConfirmed",
  "version": "1.0",
  "timestamp": "2026-02-27T10:30:30Z",
  "correlationId": "uuid-1234",
  "appName": "payment-service",
  "namespace": "production",
  "environment": "production",
  "healthCheckHistory": [
    {"timestamp": "2026-02-27T10:30:00Z", "available": 1, "desired": 3},
    {"timestamp": "2026-02-27T10:30:10Z", "available": 1, "desired": 3},
    {"timestamp": "2026-02-27T10:30:20Z", "available": 1, "desired": 3}
  ],
  "persistenceDuration": "30s"
}
```

### 5.3 CandidateResolved Event

```json
{
  "type": "CandidateResolved",
  "version": "1.0",
  "timestamp": "2026-02-27T10:31:00Z",
  "correlationId": "uuid-1234",
  "appName": "payment-service",
  "repo": "my-org/payment-service",
  "currentRevision": "def456abc789",
  "targetRevision": "abc123def456",
  "targetCommitMessage": "Fix payment processing bug",
  "targetCommitAuthor": "jane.doe@example.com",
  "targetCommitDate": "2026-02-26T15:00:00Z",
  "targetUptimePercent": 99.8,
  "ciStatusPassed": true
}
```

### 5.4 PRCreated Event

```json
{
  "type": "PRCreated",
  "version": "1.0",
  "timestamp": "2026-02-27T10:31:30Z",
  "correlationId": "uuid-1234",
  "appName": "payment-service",
  "repo": "my-org/payment-service",
  "prNumber": 42,
  "prUrl": "https://github.com/my-org/payment-service/pull/42",
  "sourceBranch": "rollback/payment-service-abc123",
  "targetBranch": "staging",
  "environment": "staging"
}
```

### 5.5 MergeCompleted Event

```json
{
  "type": "MergeCompleted",
  "version": "1.0",
  "timestamp": "2026-02-27T10:35:00Z",
  "correlationId": "uuid-1234",
  "appName": "payment-service",
  "prNumber": 42,
  "mergeSha": "abc123def456abc123def456abc123def456abc1",
  "mergeMethod": "squash",
  "autoMerged": true,
  "environment": "staging"
}
```

### 5.6 HealthRestored Event

```json
{
  "type": "HealthRestored",
  "version": "1.0",
  "timestamp": "2026-02-27T10:36:00Z",
  "correlationId": "uuid-1234",
  "appName": "payment-service",
  "argoHealth": "Healthy",
  "availableReplicas": 3,
  "desiredReplicas": 3,
  "recoveryDuration": "360s"
}
```

### 5.7 AbortEvent

```json
{
  "type": "Abort",
  "version": "1.0",
  "timestamp": "2026-02-27T10:32:00Z",
  "correlationId": "uuid-1234",
  "appName": "payment-service",
  "reason": "CI_FAILURE",
  "details": "Unit tests failed: 5 failures in payment-processor module",
  "invariantResults": {
    "I1_environment": "PASS",
    "I6_ci_success": "FAIL"
  },
  "escalationIssueUrl": "https://github.com/my-org/ai-operator-alerts/issues/123"
}
```

---

## 6. Rollback Candidate Selection Algorithm

**Function:** `selectRollbackCandidate(appName: str, currentRevision: str) -> Optional[Candidate]`

**Purpose:** Identifies the last known-good Git revision to roll back to.

### Algorithm

```
Input:
  - appName: Name of Argo CD application
  - currentRevision: Current Git SHA causing degradation

Output:
  - Candidate object with targetRevision, or None if no candidate found

Steps:

1. Get Argo CD application configuration
   - Call: argocd.getApplicationSyncStatus(appName)
   - Extract: targetBranch (e.g., "main", "staging")
   - Extract: repo (e.g., "my-org/payment-service")

2. Query Git commit history on targetBranch
   - Use GitHub API to list commits
   - Time range: last 30 days
   - Exclude: commits after currentRevision (only ancestors)
   - Sort: newest to oldest
   - Limit: 50 commits

3. For each candidate commit (iterate newest to oldest):

   a. Verify it's older than current
      - Check: candidate_sha is ancestor of currentRevision
      - If not: skip candidate

   b. Check CI status
      - Call: github.getPullRequestStatus for PR that introduced this commit
      - Or: Check GitHub commit status API
      - Required: CI status == "success"
      - If failed: skip candidate

   c. Check historical stability (if metrics available)
      - Query Prometheus:
        * Metric: avg_over_time(up{app=appName, revision=candidate_sha}[24h])
        * Time range: When this revision was deployed
      - Threshold: >= 0.99 (99% uptime)
      - If < 99%: skip candidate
      - If metrics unavailable: Accept if CI passed (fallback)

   d. If all checks pass:
      - Fetch commit metadata (message, author, date)
      - Return Candidate{
          targetRevision: candidate_sha,
          commitMessage: message,
          commitAuthor: author,
          commitDate: date,
          uptimePercent: calculated_uptime
        }

4. If no candidate found after checking 50 commits:
   - Log: "No stable rollback candidate found for {appName}"
   - Emit: NoCandidateFound event
   - Return: None

5. Edge Cases:
   - If currentRevision not found in branch: abort (log error)
   - If Git API fails: retry 3 times, then abort
   - If Prometheus unavailable: use CI-only fallback
   - If all candidates fail CI: abort, alert human
```

### Example Execution

**Scenario:** `payment-service` degraded at revision `def456`

```
1. Get Argo CD config
   - targetBranch: "main"
   - repo: "my-org/payment-service"

2. List commits on main (last 30 days)
   - def456 (current, degraded) ← skip
   - ccc111 (2 hours ago)
   - bbb222 (1 day ago)
   - abc123 (2 days ago)
   - ...

3. Check ccc111
   - Ancestor check: ✓ (is ancestor of def456)
   - CI status: ✗ (failed)
   - Skip ccc111

4. Check bbb222
   - Ancestor check: ✓
   - CI status: ✓
   - Prometheus query: avg(up{app=payment-service, revision=bbb222}) = 0.95
   - Uptime check: ✗ (95% < 99%)
   - Skip bbb222

5. Check abc123
   - Ancestor check: ✓
   - CI status: ✓
   - Prometheus query: avg(up{..., revision=abc123}) = 0.998
   - Uptime check: ✓ (99.8% >= 99%)
   - Return: Candidate{
       targetRevision: "abc123",
       commitMessage: "Fix payment processing bug",
       commitAuthor: "jane.doe",
       commitDate: "2026-02-25T10:00:00Z",
       uptimePercent: 99.8
     }
```

---

## 7. Module Communication Architecture

### 7.1 Event Bus (NATS)

**Technology:** NATS JetStream (persistent messaging)

**Topic Structure:**
```
ai-operator.{environment}.degradation.detected
ai-operator.{environment}.degradation.confirmed
ai-operator.{environment}.candidate.resolved
ai-operator.{environment}.pr.created
ai-operator.{environment}.merge.completed
ai-operator.{environment}.health.restored
ai-operator.{environment}.abort
ai-operator.{environment}.fsm.state_transition
```

**Example:** `ai-operator.staging.degradation.detected`

**Subscription Pattern:**
- Modules subscribe to specific topics
- FSM Controller subscribes to all topics (wildcard: `ai-operator.*.*.>`)

**Message Retention:** 7 days (for replay/debugging)

**Delivery Guarantee:** At-least-once (idempotent consumers required)

### 7.2 Shared State Store (etcd)

**Technology:** etcd v3.5+

**Key Pattern:**
```
/ai-operator/rollback/{appName}/{correlationId}/state
/ai-operator/rollback/{appName}/{correlationId}/metadata
/ai-operator/rollback/{appName}/{correlationId}/events
```

**Example:**
```
Key: /ai-operator/rollback/payment-service/uuid-1234/state
Value: {
  "fsmState": "PRCreated",
  "appName": "payment-service",
  "correlationId": "uuid-1234",
  "environment": "staging",
  "prNumber": 42,
  "targetRevision": "abc123",
  "createdAt": "2026-02-27T10:30:00Z",
  "updatedAt": "2026-02-27T10:31:30Z"
}
```

**TTL:** 7 days (auto-cleanup after completion/abort)

**Consistency:** Strong consistency (quorum reads/writes)

---

## 8. Timing Configuration

All timing constants are defined in [`ai-operator-architecture.md`](./ai-operator-architecture.md) Section 7, Glossary.

**Summary Table:**

| Constant | Value | Module |
|----------|-------|--------|
| `HEALTH_CHECK_INTERVAL` | 10 seconds | DegradationDetector |
| `DEGRADATION_PERSISTENCE_CHECKS` | 3 checks | StabilityAnalyzer |
| `HEALTH_RESTORATION_DURATION` | 60 seconds | HealthMonitor |
| `CANDIDATE_SEARCH_TIMEOUT` | 120 seconds | RollbackCandidateResolver |
| `CI_WAIT_TIMEOUT` | 300 seconds | MergeController |
| `MERGE_APPROVAL_TIMEOUT` | 3600 seconds | MergeController (production) |
| `MCP_RETRY_COUNT` | 3 retries | All modules |
| `MCP_RETRY_BACKOFF` | Exponential (1s, 2s, 4s) | All modules |

**Derived Values:**
- Total degradation detection time: 30 seconds (3 checks × 10s)
- Max time from detection to PR creation: 150s (30s + 120s)
- Max time from PR creation to merge (staging): 300s (CI wait)
- Max time from merge to completion: 660s (600s health + 60s confirmation)

---

## 9. State Persistence and Recovery

### 9.1 State Persistence

**When:** After every FSM state transition

**Where:** etcd at key `/ai-operator/rollback/{appName}/{correlationId}/state`

**Format:**
```json
{
  "fsmState": "PRCreated",
  "appName": "payment-service",
  "correlationId": "uuid-1234",
  "environment": "staging",
  "repo": "my-org/payment-service",
  "currentRevision": "def456",
  "targetRevision": "abc123",
  "prNumber": 42,
  "prUrl": "https://github.com/my-org/payment-service/pull/42",
  "createdAt": "2026-02-27T10:30:00Z",
  "updatedAt": "2026-02-27T10:31:30Z",
  "metadata": {
    "healthCheckHistory": [...],
    "invariantResults": {...}
  }
}
```

### 9.2 Crash Recovery

**On AI Operator Startup:**

```python
def recover_state():
    # 1. Read all active rollback attempts from etcd
    active_rollbacks = etcd.get_prefix("/ai-operator/rollback/")

    for rollback in active_rollbacks:
        correlation_id = rollback.correlationId
        fsm_state = rollback.fsmState
        age = now() - rollback.createdAt

        # 2. Check age
        if age > 24 * 3600:  # 24 hours
            log.error(f"Rollback {correlation_id} older than 24h, aborting")
            transition_to_abort(correlation_id, reason="STALE_STATE")
            alert_human(correlation_id, "Stale rollback attempt found during recovery")
            continue

        # 3. Resume based on state
        if fsm_state == "PRCreated":
            # Verify PR still open
            pr_status = mcp.call("github.getPullRequestStatus", {
                "repo": rollback.repo,
                "prNumber": rollback.prNumber
            })
            if pr_status.state != "open":
                log.warn(f"PR {rollback.prNumber} no longer open, aborting")
                transition_to_abort(correlation_id, reason="PR_CLOSED")
            else:
                # Resume monitoring CI
                resume_merge_controller(rollback)

        elif fsm_state == "RollbackMerged":
            # Check current health
            health = mcp.call("argocd.getApplicationHealth", {
                "appName": rollback.appName
            })
            if health.status == "Healthy":
                transition_to_complete(correlation_id)
            else:
                # Resume health monitoring
                resume_health_monitor(rollback)

        elif fsm_state == "AwaitingMergeApproval":
            # Resume waiting for approval
            resume_merge_controller(rollback)

        else:
            log.info(f"Resuming rollback {correlation_id} in state {fsm_state}")
            # Re-initialize FSM at saved state
            fsm.restore_state(rollback)

    log.info(f"Recovered {len(active_rollbacks)} rollback attempts")
```

### 9.3 State Cleanup

**RollbackComplete State:**
- Wait 60 seconds (for final metrics emission)
- Delete from etcd: `/ai-operator/rollback/{appName}/{correlationId}/*`

**Abort State:**
- Mark as aborted (set field `aborted: true`)
- Retain in etcd for 7 days (for post-mortem analysis)
- After 7 days: auto-delete via TTL

**Idle State:**
- No active state in etcd (clean slate)

---

## 10. Observability & Metrics

### 10.1 Prometheus Metrics

```yaml
# Counters
ai_operator_degradations_detected_total:
  type: counter
  labels: [app_name, environment]
  description: "Total degradations detected"

ai_operator_rollbacks_proposed_total:
  type: counter
  labels: [app_name, environment, success]
  description: "Total rollback PRs created (success=true/false)"

ai_operator_rollbacks_merged_total:
  type: counter
  labels: [app_name, environment, auto_merged]
  description: "Total rollback PRs merged"

ai_operator_rollbacks_aborted_total:
  type: counter
  labels: [app_name, environment, reason]
  description: "Total rollback attempts aborted"

ai_operator_invariant_violations_total:
  type: counter
  labels: [invariant_id]
  description: "Total invariant violations"

ai_operator_mcp_call_errors_total:
  type: counter
  labels: [call_name, error_code]
  description: "Total MCP call errors"

# Gauges
ai_operator_fsm_state:
  type: gauge
  labels: [app_name, state]
  description: "Current FSM state (1 = in this state, 0 = not)"

ai_operator_active_rollbacks:
  type: gauge
  labels: [environment]
  description: "Number of active rollback attempts"

# Histograms
ai_operator_mean_time_to_recovery_seconds:
  type: histogram
  labels: [app_name, environment]
  buckets: [30, 60, 120, 300, 600, 1200]
  description: "Time from degradation detection to health restoration"

ai_operator_candidate_resolution_duration_seconds:
  type: histogram
  labels: [app_name]
  buckets: [10, 30, 60, 120]
  description: "Time to find rollback candidate"

ai_operator_mcp_call_duration_seconds:
  type: histogram
  labels: [call_name]
  buckets: [0.1, 0.5, 1, 2, 5, 10]
  description: "MCP call latency"
```

### 10.2 Key Metrics to Monitor

| Metric | Alert Threshold | Meaning |
|--------|----------------|---------|
| `ai_operator_rollbacks_aborted_total` | > 2 in 1h | High abort rate, investigate |
| `ai_operator_mean_time_to_recovery_seconds` | p95 > 600s | Slow recovery, check CI/candidate selection |
| `ai_operator_invariant_violations_total{invariant_id="I6_ci_success"}` | > 5 in 1h | CI frequently failing on rollback PRs |
| `ai_operator_mcp_call_errors_total{error_code="PERMISSION_DENIED"}` | > 0 | Permissions issue, fix immediately |

### 10.3 Logging

**Format:** Structured JSON logs

**Fields:**
- `timestamp`: ISO 8601
- `level`: DEBUG, INFO, WARN, ERROR
- `module`: Module name (e.g., "DegradationDetector")
- `correlationId`: UUID for rollback attempt
- `message`: Human-readable message
- `metadata`: Additional context

**Example:**
```json
{
  "timestamp": "2026-02-27T10:30:00Z",
  "level": "INFO",
  "module": "DegradationDetector",
  "correlationId": "uuid-1234",
  "message": "Degradation detected for payment-service",
  "metadata": {
    "appName": "payment-service",
    "argoHealth": "Degraded",
    "availableReplicas": 1,
    "desiredReplicas": 3
  }
}
```

---

## 11. Testing Strategy

### 11.1 Unit Tests

**FSM Transitions:**
- Test all state transitions in Section 3.1 table
- Verify guard conditions correctly block/allow transitions
- Verify actions are executed on transitions
- Test invalid transitions (should be rejected)

**Rollback Candidate Selection:**
- Test with 0 candidates (should abort)
- Test with 1 candidate (should select)
- Test with multiple candidates (should pick newest stable)
- Test with CI failures (should skip failed commits)
- Test with metrics unavailable (should fall back to CI-only)
- Test with all candidates failing (should abort)

**Invariant Enforcement:**
- Test each invariant individually (pass and fail cases)
- Test with all invariants passing (should allow auto-merge)
- Test with one invariant failing (should block auto-merge)

**Replica Mismatch Logic:**
- Test availableReplicas < desiredReplicas (should detect degradation)
- Test availableReplicas == desiredReplicas (should not detect)
- Test desiredReplicas == 0 (should not detect - intentional scale-down)

### 11.2 Integration Tests

**Simulated Argo CD Degraded States:**
- Mock Argo CD API to return "Degraded" health
- Verify DegradationDetector emits event
- Verify StabilityAnalyzer requires 3 checks
- Verify end-to-end flow to PR creation

**CI Scenarios:**
- Mock GitHub PR with CI pending → success (should merge)
- Mock GitHub PR with CI pending → failure (should abort)
- Mock GitHub PR with CI timeout (should abort after 300s)

**Concurrent PRs:**
- Create 2 rollback PRs for same app (should block second)
- Verify I7_no_conflicts invariant fails

**Environment-Specific Paths:**
- Staging: Verify auto-merge after CI passes
- Production: Verify human alert, no auto-merge

### 11.3 Chaos Tests

**Flapping Health:**
- Alternate Degraded ↔ Healthy every 10s
- Verify StabilityAnalyzer requires 3 consecutive degraded checks
- Verify no false positive rollbacks

**Multiple Degradations:**
- Degrade 5 apps simultaneously
- Verify separate correlation IDs
- Verify no cross-contamination of state

**Rollback Under Load:**
- Degrade app during high traffic
- Verify rollback completes within SLA
- Verify no additional degradations from rollback itself

**Network Failures:**
- Simulate GitHub API timeouts during createPullRequest
- Verify retry with exponential backoff
- Verify abort after 3 failures

**etcd Failure:**
- Stop etcd during active rollback
- Restart etcd
- Verify state recovery on AI Operator restart

### 11.4 Test Fixtures

**Mock Data Files:**
- `fixtures/degraded-app.json` - Argo CD degraded response
- `fixtures/healthy-app.json` - Argo CD healthy response
- `fixtures/deployment-status.json` - K8s deployment with 1/3 replicas
- `fixtures/git-commits.json` - Sample commit history
- `fixtures/pr-response.json` - GitHub PR creation response

**Test Environments:**
- `test-staging` - Full stack with mocked MCP providers
- `test-chaos` - Chaos mesh for network failures

---

## 12. Sequence Diagrams

### 12.1 Staging Auto-Merge Happy Path

```
┌────────────┐  ┌─────────────┐  ┌──────────────┐  ┌──────────┐  ┌────────┐
│Degradation │  │  Stability  │  │   Rollback   │  │    PR    │  │ Merge  │
│ Detector   │  │  Analyzer   │  │  Candidate   │  │Generator │  │Control │
└─────┬──────┘  └──────┬──────┘  └──────┬───────┘  └────┬─────┘  └───┬────┘
      │                │                 │               │             │
      │ poll Argo      │                 │               │             │
      ├────────────────>                 │               │             │
      │ Degraded       │                 │               │             │
      │<────────────────                 │               │             │
      │                │                 │               │             │
      │ publish degradation.detected     │               │             │
      ├───────────────>│                 │               │             │
      │                │                 │               │             │
      │                │ wait 10s        │               │             │
      │ poll Argo      │                 │               │             │
      ├────────────────>                 │               │             │
      │ Degraded       │                 │               │             │
      │<────────────────                 │               │             │
      │                │                 │               │             │
      │ publish degradation.detected     │               │             │
      ├───────────────>│                 │               │             │
      │                │                 │               │             │
      │                │ wait 10s        │               │             │
      │ poll Argo      │                 │               │             │
      ├────────────────>                 │               │             │
      │ Degraded       │                 │               │             │
      │<────────────────                 │               │             │
      │                │                 │               │             │
      │ publish degradation.detected     │               │             │
      ├───────────────>│                 │               │             │
      │                │ 3 checks! ✓     │               │             │
      │                │                 │               │             │
      │                │ publish degradation.confirmed   │             │
      │                ├────────────────>│               │             │
      │                │                 │               │             │
      │                │                 │ query Git     │             │
      │                │                 │ query metrics │             │
      │                │                 │ find abc123   │             │
      │                │                 │               │             │
      │                │                 │ publish candidate.resolved  │
      │                │                 ├──────────────>│             │
      │                │                 │               │             │
      │                │                 │               │ create PR   │
      │                │                 │               │ on GitHub   │
      │                │                 │               │             │
      │                │                 │               │ publish pr.created
      │                │                 │               ├────────────>│
      │                │                 │               │             │
      │                │                 │               │             │ wait for CI
      │                │                 │               │             │ (poll every 5s)
      │                │                 │               │             │
      │                │                 │               │             │ CI success! ✓
      │                │                 │               │             │
      │                │                 │               │             │ check invariants
      │                │                 │               │             │ all pass! ✓
      │                │                 │               │             │
      │                │                 │               │             │ merge PR
      │                │                 │               │             │
      │                │                 │               │             │ publish merge.completed
      │                │                 │               │             │
      │ poll Argo      │                 │               │             │
      ├────────────────>                 │               │             │
      │ Healthy! ✓     │                 │               │             │
      │<────────────────                 │               │             │
      │                │                 │               │             │
      │ publish health.restored          │               │             │
      │                │                 │               │             │
      │                FSM → RollbackComplete            │             │
      │                                                  │             │
```

### 12.2 Production Manual Approval Path

```
┌────────────┐  ┌──────────┐  ┌────────┐  ┌─────────┐
│    PR      │  │  Merge   │  │  Human  │  │  Audit  │
│ Generator  │  │ Control  │  │ Approver│  │   Log   │
└─────┬──────┘  └────┬─────┘  └────┬────┘  └────┬────┘
      │              │              │            │
      │ publish pr.created          │            │
      ├─────────────>│              │            │
      │              │              │            │
      │              │ wait CI      │            │
      │              │ CI success ✓ │            │
      │              │              │            │
      │              │ check env    │            │
      │              │ = production │            │
      │              │              │            │
      │              │ alert human  │            │
      │              ├─────────────>│            │
      │              │ GitHub issue │            │
      │              │ Slack alert  │            │
      │              │              │            │
      │              │              │ review PR  │
      │              │              │            │
      │              │<─────────────┤            │
      │              │   APPROVE    │            │
      │              │              │            │
      │              │ log approval │            │
      │              ├─────────────────────────>│
      │              │              │            │
      │              │ merge PR     │            │
      │              │              │            │
      │              │ publish merge.completed   │
      │              │              │            │
```

---

## Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0.1 | (Original) | - | Initial controller architecture |
| 2.0 | 2026-02-27 | AI DevOps Team | Complete rewrite: FSM table, event schemas, algorithms, timing, persistence |

---

**Related Documents:**
- [Architecture](./ai-operator-architecture.md) - System overview and glossary
- [MCP API Specification](./ai-operator-mcp-api-spec.md) - MCP call details
- [Governance Specification](./ai-operator-governance-spec.md) - Invariants and audit logging
- [Deployment Guide](./ai-operator-deployment-guide.md) - Installation and operations
