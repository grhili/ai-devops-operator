# AI Operator Full Context - Operational Specification

**Version:** 2.0
**Last Updated:** 2026-02-27
**Status:** Production-Ready Operational Guide
**Audience:** SREs, Operators, DevOps Engineers

---

## Table of Contents

1. [Overview](#1-overview)
2. [Role Hierarchy and Permissions](#2-role-hierarchy-and-permissions)
3. [Promotion PR Rules](#3-promotion-pr-rules)
4. [Rollback PR Rules](#4-rollback-pr-rules)
5. [CI Verification Rules](#5-ci-verification-rules)
6. [Argo CD Application Health Rules](#6-argo-cd-application-health-rules)
7. [Acceptance Criteria](#7-acceptance-criteria)
8. [AI vs Human Responsibilities](#8-ai-vs-human-responsibilities)
9. [Worked Examples](#9-worked-examples)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Overview

This document provides **operational context** for the AI DevOps Operator, defining:
- Role-based permissions and responsibilities
- Pull request workflows (promotion and rollback)
- CI/CD pipeline requirements
- Argo CD health monitoring rules
- Acceptance criteria for rollbacks
- Worked examples of common scenarios

**Prerequisites:** Read [`ai-operator-architecture.md`](./ai-operator-architecture.md) for system overview and [`ai-operator-governance-spec.md`](./ai-operator-governance-spec.md) for governance rules.

---

## 2. Role Hierarchy and Permissions

### 2.1 Role Definitions

| Role | GitHub Permissions | Approval Rights | AI Operator Mapping | Count Required |
|------|-------------------|-----------------|---------------------|----------------|
| **Developer** | Create PRs, comment | None | - | N/A |
| **Approver** | All developer + approve | Can approve **promotion PRs** | - | **2 required** for promotions |
| **Senior Maintainer** | All approver + admin | Can approve **rollback PRs** | - | **1 required** for rollbacks |
| **AI Operator** | Create PRs, read repo/cluster | **Auto-merge in staging only** (per governance) | This system | N/A |

**Key Differences:**
- **Approvers** handle day-to-day promotions (staging → production)
- **Senior Maintainers** handle emergency rollbacks (require deeper system knowledge)
- **AI Operator** automates rollbacks in staging, creates PRs for production

### 2.2 Permission Matrix

| Action | Developer | Approver | Senior Maintainer | AI Operator |
|--------|-----------|----------|-------------------|-------------|
| Create promotion PR | ✅ | ✅ | ✅ | ❌ |
| Create rollback PR | ❌ | ❌ | ✅ | ✅ (automated) |
| Approve promotion PR | ❌ | ✅ | ✅ | ❌ |
| Approve rollback PR | ❌ | ❌ | ✅ | ❌ |
| Merge staging rollback | ❌ | ❌ | ✅ | ✅ (auto, if invariants pass) |
| Merge production rollback | ❌ | ❌ | ✅ | ❌ (requires human) |
| Force-push to main | ❌ | ❌ | ❌ | ❌ |
| Modify branch protection | ❌ | ❌ | ✅ | ❌ |

---

## 3. Promotion PR Rules

**Purpose:** Promote changes from development to production branches.

**Typical Flow:** `develop` → `staging` → `main` (production)

### 3.1 Rules

| Rule ID | Description | Enforcement Mechanism |
|---------|-------------|----------------------|
| **PR-01** | Promotion PRs must be created from `staging` to `main` branch | CI pipeline branch checks |
| **PR-02** | Promotion PRs must include a changelog entry describing changes | PR template validation |
| **PR-03** | Promotion PRs must pass all CI tests before merge | GitHub branch protection (required status checks) |
| **PR-04** | Promotion PRs must be approved by at least **2 approvers** | GitHub branch protection (required reviews) |
| **PR-05** | Promotion PRs must include reference to corresponding issue or ticket | PR template validation |
| **PR-06** | Promotion PRs cannot be created if target environment is degraded | CI check queries Argo CD health |
| **PR-07** | Promotion PRs must have label `promotion` | PR template default |

### 3.2 Example Promotion PR

**Title:** `Promotion: Release v1.2.3 to production`

**Description:**
```markdown
## Release Summary

**Version:** v1.2.3
**Jira Ticket:** PROJ-456

### Changes Included

- Feature: Add user authentication (#123)
- Fix: Resolve payment processing bug (#124)
- Chore: Update dependencies (#125)

### Testing

- ✅ Unit tests passed (100% coverage)
- ✅ Integration tests passed
- ✅ Security scan passed (no vulnerabilities)
- ✅ Staging deployment verified healthy for 48 hours

### Rollback Plan

If issues arise, rollback PR will revert to `abc123def456` (previous production SHA).

### Approvals Required

- [x] Approver 1: @alice
- [ ] Approver 2: @bob
```

**Note:** AI Operator does **not** create promotion PRs - these are always human-initiated.

---

## 4. Rollback PR Rules

**Purpose:** Revert a deployment in case of degradation or issues.

**Trigger:** Application enters `Degraded` state in Argo CD.

### 4.1 Rules

| Rule ID | Description | Enforcement Mechanism |
|---------|-------------|----------------------|
| **RB-01** | Rollback PRs must target the branch where problematic change was merged | CI pipeline branch checks |
| **RB-02** | Rollback PRs must reference the PR or commit SHA being reverted | PR template validation |
| **RB-03** | Rollback PRs must pass all CI tests before merge | GitHub branch protection |
| **RB-04** | Rollback PRs require approval by **1 senior maintainer** (production only) | GitHub branch protection |
| **RB-05** | Rollback PRs must include detailed explanation of rollback reason | PR template validation |
| **RB-06** | Rollback PRs must have labels `ai-operator`, `rollback`, `{environment}` | AI Operator enforced |
| **RB-07** | Rollback PRs in **staging** can be auto-merged if all invariants pass | AI Operator governance (Section 4 of governance spec) |
| **RB-08** | Rollback PRs in **production** require human approval (no auto-merge) | AI Operator governance |
| **RB-09** | Rollback PRs cannot be created if target revision is same as current | AI Operator validation |
| **RB-10** | Only one rollback PR per application per target branch at a time | AI Operator enforced (invariant I7) |

### 4.2 Example Rollback PR (AI-Generated)

**Title:** `Rollback: payment-service to abc123 (degraded in staging)`

**Description:**
```markdown
## 🤖 Automated Rollback

**Triggered by:** AI DevOps Operator
**Correlation ID:** uuid-1234-5678-90ab-cdef
**Environment:** staging
**Timestamp:** 2026-02-27T10:30:00Z

---

### Degradation Details

- **Application:** payment-service
- **Current Revision:** def456abc789 (deployed 2 hours ago)
- **Argo CD Health:** Degraded
- **Replica Status:** 1/3 available
- **Persistence:** Degraded for 3 consecutive health checks (30 seconds)

### Rollback Target

- **Target Revision:** abc123def456
- **Commit Message:** Fix payment processing bug
- **Commit Author:** jane.doe@example.com
- **Commit Date:** 2026-02-26T15:00:00Z
- **Stability Score:** 99.8% uptime over 24h when deployed

### Changes

```diff
# .argocd/application.yaml
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: payment-service
spec:
  source:
-   targetRevision: def456abc789
+   targetRevision: abc123def456
```

### CI Status

CI checks must pass before merge:
- [ ] Unit tests
- [ ] Integration tests
- [ ] Security scan

### Merge Strategy

**AUTO-MERGE ENABLED (Staging Environment)**

This PR will be **automatically merged** after CI passes if all safety invariants are satisfied:
- ✅ I1: Environment is staging
- ✅ I2: Argo CD health is Degraded
- ✅ I3: Available replicas < desired
- ✅ I4: Degradation persists for 3 checks
- ✅ I5: Target revision has 99% historical uptime
- ⏳ I6: CI status is success (pending)
- ✅ I7: No conflicting PRs
- ⏳ I8: Branch protection satisfied (pending CI)

---

**Audit Log:** See Elasticsearch index `ai-operator-audit-2026.02.27` for full decision trail.
**Correlation ID:** `uuid-1234-5678-90ab-cdef`
```

---

## 5. CI Verification Rules

**Purpose:** Ensure all PRs (promotion and rollback) pass required tests before merge.

### 5.1 Rules

| Rule ID | Description | Enforcement Mechanism |
|---------|-------------|----------------------|
| **CI-01** | All PRs must trigger the full CI pipeline | GitHub Actions workflow triggers |
| **CI-02** | CI must include unit tests, integration tests, and security scans | CI configuration (`.github/workflows/pr-checks.yml`) |
| **CI-03** | CI must verify compliance with MCP policies (if applicable) | Custom MCP compliance checker |
| **CI-04** | CI must verify no secrets or sensitive data are included | Secret scanning tools (GitGuardian, Trivy) |
| **CI-05** | CI must generate test coverage report | Coverage tool (pytest-cov, Istanbul) |
| **CI-06** | CI must complete within 10 minutes (timeout) | GitHub Actions timeout setting |
| **CI-07** | CI failure blocks merge (required status check) | GitHub branch protection |
| **CI-08** | CI must re-run on every new commit to PR | GitHub Actions trigger on push |

### 5.2 CI Pipeline Configuration Example

**File:** `.github/workflows/pr-checks.yml`

```yaml
name: PR Checks
on:
  pull_request:
    types: [opened, synchronize, reopened]

jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - name: Checkout code
        uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install pytest pytest-cov

      - name: Run unit tests
        run: pytest tests/unit --cov=src --cov-report=xml

      - name: Run integration tests
        run: pytest tests/integration

      - name: Security scan
        uses: aquasecurity/trivy-action@master
        with:
          scan-type: 'fs'
          scan-ref: '.'
          format: 'sarif'
          output: 'trivy-results.sarif'

      - name: Upload coverage report
        uses: codecov/codecov-action@v3
        with:
          files: ./coverage.xml

      - name: MCP compliance check (if applicable)
        run: |
          python scripts/mcp_compliance_checker.py

  lint:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v3

      - name: Lint code
        run: |
          pip install flake8
          flake8 src/ --max-line-length=120

  branch-protection-check:
    runs-on: ubuntu-latest
    steps:
      - name: Verify target branch
        run: |
          if [[ "${{ github.base_ref }}" != "main" && "${{ github.base_ref }}" != "staging" ]]; then
            echo "Error: PRs must target 'main' or 'staging' branch"
            exit 1
          fi

      - name: Check for rollback prefix (if rollback PR)
        run: |
          if [[ "${{ github.head_ref }}" == rollback/* ]]; then
            echo "Rollback PR detected"
            # Additional rollback-specific checks here
          fi
```

**Required Status Checks (Branch Protection):**
- `test`
- `lint`
- `branch-protection-check`

---

## 6. Argo CD Application Health Rules

**Purpose:** Monitor application health and trigger rollbacks when degraded.

### 6.1 Health Status Definitions

| Status | Meaning | AI Operator Action |
|--------|---------|-------------------|
| **Healthy** | All resources healthy, desired state met | No action (normal state) |
| **Progressing** | Sync in progress, waiting for rollout | No action (wait for completion) |
| **Degraded** | Resources unhealthy or desired state not met | **Trigger rollback** (if persistent) |
| **Suspended** | Application paused | No action (intentional state) |
| **Missing** | Resources not found | Alert human (configuration error) |
| **Unknown** | Cannot determine health | Alert human (investigate) |

### 6.2 Rules

| Rule ID | Description | Enforcement Mechanism |
|---------|-------------|----------------------|
| **AC-01** | Applications must be in `Healthy` or `Progressing` status post-deployment | Argo CD health status checks |
| **AC-02** | Applications in `Degraded` status trigger alerts | AI Operator DegradationDetector module |
| **AC-03** | Rollback must be triggered if application degraded for **3 consecutive 10-second health checks** (30 seconds total) | AI Operator StabilityAnalyzer module |
| **AC-04** | Health checks must include readiness and liveness probes | Kubernetes deployment manifests |
| **AC-05** | Application manifests must define replica counts | Kubernetes deployment spec |
| **AC-06** | Health degradation in production triggers immediate alert (no auto-merge) | AI Operator escalation procedure |
| **AC-07** | Health restoration before merge causes PR to be closed | AI Operator MergeController logic |

**Timing Details (Reconciled):**
- **Health check interval:** 10 seconds (per application)
- **Persistence threshold:** 3 consecutive checks = 30 seconds total
- **Not "10 minutes"** - that was an error in the original spec

---

## 7. Acceptance Criteria

**Purpose:** Define conditions under which rollback is justified and successful.

### 7.1 Rollback Trigger Criteria

| Criteria ID | Description | Verification |
|-------------|-------------|--------------|
| **AC-01** | Application health status is `Degraded` | `argocd.getApplicationHealth().status == "Degraded"` |
| **AC-02** | Available replicas < desired replicas | `kubernetes.getDeploymentStatus().availableReplicas < .desiredReplicas` |
| **AC-03** | Degradation persists for 3 consecutive 10-second checks (30 seconds total) | StabilityAnalyzer verifies history |
| **AC-04** | Rollback target revision exists and is older than current | Git ancestry check |
| **AC-05** | Rollback target has 99% uptime when previously deployed | Prometheus query OR CI passed |

### 7.2 Rollback Success Criteria

| Criteria ID | Description | Verification |
|-------------|-------------|--------------|
| **AC-06** | Rollback PR created within 2 minutes of confirmed degradation | Timestamp tracking |
| **AC-07** | Rollback PR CI passes within 5 minutes | GitHub Actions completion |
| **AC-08** | Rollback PR merged (auto in staging, manual in prod) | GitHub merge status |
| **AC-09** | Application health returns to `Healthy` within 10 minutes post-merge | Argo CD health monitoring |
| **AC-10** | No new degradations introduced by rollback | Health monitoring for 60 seconds post-recovery |

---

## 8. AI vs Human Responsibilities

**Clear Separation of Duties:**

### 8.1 AI Operator Responsibilities

| Responsibility | Environment | Details |
|---------------|-------------|---------|
| **Monitor health** | All | Poll Argo CD every 10s, detect degradations |
| **Analyze stability** | All | Verify 3 consecutive degraded checks |
| **Find rollback candidate** | All | Search Git history, check CI, query metrics |
| **Create rollback PR** | All | Generate PR with detailed context |
| **Merge rollback PR** | **Staging only** | Auto-merge if all 8 invariants pass |
| **Monitor recovery** | All | Verify health restoration post-merge |
| **Escalate to humans** | All | Alert on invariant violations, errors, or production degradations |
| **Log all actions** | All | Immutable audit trail in Elasticsearch |

### 8.2 Human Responsibilities

| Responsibility | Role | Details |
|---------------|------|---------|
| **Approve promotion PRs** | Approver (2 required) | Review staging → production promotions |
| **Approve production rollback PRs** | Senior Maintainer (1 required) | Review and approve AI-created rollback PRs in production |
| **Resolve conflicts** | Senior Maintainer | Handle merge conflicts in rollback PRs |
| **Investigate aborts** | Senior Maintainer | Review FSM aborts, fix underlying issues |
| **Modify governance** | Admin | Update invariants, RBAC, or AI Operator configuration |
| **Override AI decisions** | Senior Maintainer | Comment `ABORT` or `MANUAL` on escalation issues |
| **Create promotion PRs** | Developer | Initiate staging → production promotions |

**Key Principle:** AI **proposes** and **automates** (staging), humans **approve** and **oversee** (production).

---

## 9. Worked Examples

### 9.1 Example 1: Promotion PR Workflow (Human-Initiated)

**Scenario:** Deploy new feature from staging to production.

**Steps:**

1. **Developer creates PR:** `staging` → `main`
   - Title: `Promotion: Release v1.2.3 to production`
   - Includes changelog, references PROJ-456

2. **CI pipeline runs:**
   - Unit tests: ✅ Passed
   - Integration tests: ✅ Passed
   - Security scan: ✅ Passed
   - MCP compliance: ✅ Passed

3. **Approvers review:**
   - Approver 1 (@alice): ✅ Approved
   - Approver 2 (@bob): ✅ Approved

4. **PR merged:**
   - GitHub merges PR to `main`
   - Argo CD syncs to production

5. **AI Operator monitors:**
   - Polls Argo CD health every 10s
   - Application status: `Healthy` ✅
   - No action needed

**Result:** Successful promotion, no rollback required.

---

### 9.2 Example 2: Automated Rollback PR Workflow (Staging)

**Scenario:** Application degrades in staging after deployment.

**Steps:**

1. **AI Operator detects degradation:**
   - Time: 10:30:00 - Argo CD health: `Degraded`, replicas: 1/3
   - Publishes `degradation.detected` event

2. **AI Operator verifies persistence:**
   - Time: 10:30:10 - Still degraded (check 2)
   - Time: 10:30:20 - Still degraded (check 3)
   - **3 consecutive checks confirmed** → Publishes `degradation.confirmed`

3. **AI Operator finds candidate:**
   - Queries Git history: finds `abc123` (2 days old)
   - Checks CI: ✅ Passed
   - Checks metrics: 99.8% uptime when deployed
   - Publishes `candidate.resolved`

4. **AI Operator creates PR:**
   - Source branch: `rollback/payment-service-abc123`
   - Target branch: `staging`
   - Title: `Rollback: payment-service to abc123 (degraded in staging)`
   - Labels: `ai-operator`, `rollback`, `staging`
   - Publishes `pr.created`

5. **CI pipeline runs:**
   - Unit tests: ✅ Passed
   - Integration tests: ✅ Passed
   - Security scan: ✅ Passed
   - Time: 10:33:00 - CI complete

6. **AI Operator checks invariants:**
   - I1_environment: ✅ PASS (staging)
   - I2_health_degraded: ✅ PASS (still degraded)
   - I3_replica_shortage: ✅ PASS (1 < 3)
   - I4_persistence: ✅ PASS (3 checks)
   - I5_stable_previous: ✅ PASS (99.8% uptime)
   - I6_ci_success: ✅ PASS (CI passed)
   - I7_no_conflicts: ✅ PASS (only 1 PR)
   - I8_branch_protection: ✅ PASS (mergeable)
   - **All invariants pass** → Proceed with auto-merge

7. **AI Operator merges PR:**
   - Calls `github.mergePullRequest(prNumber=42)`
   - Publishes `merge.completed`

8. **AI Operator monitors recovery:**
   - Time: 10:35:00 - Argo CD syncs rollback
   - Time: 10:35:30 - Replicas: 3/3, health: `Healthy` ✅
   - Time: 10:36:30 - Still healthy (60s confirmation)
   - Publishes `health.restored`
   - FSM transitions to `RollbackComplete`

**Result:** Successful automated rollback in staging, 6 minutes from detection to recovery.

---

### 9.3 Example 2b: Automated Rollback PR Workflow (Production)

**Scenario:** Application degrades in production after deployment.

**Steps:**

1-4. **Same as Example 2** (detection, persistence, candidate, PR creation)

5. **AI Operator escalates to human:**
   - Environment: `production` → **No auto-merge allowed**
   - Creates GitHub issue: `[URGENT] AI Operator Escalation: payment-service - Degraded`
   - Sends Slack alert: `@oncall AI Operator needs approval for rollback PR #42`
   - FSM transitions to `AwaitingMergeApproval`

6. **Senior Maintainer reviews:**
   - Reviews PR #42
   - Verifies degradation in Argo CD
   - Checks CI passed: ✅
   - Approves PR

7. **AI Operator detects approval:**
   - Polls PR status: `approvals: 1, requiredApprovals: 1`
   - Calls `github.mergePullRequest(prNumber=42)`

8. **AI Operator monitors recovery:**
   - Application health returns to `Healthy` ✅
   - FSM transitions to `RollbackComplete`

**Result:** Human-approved rollback in production, ~10 minutes from detection to recovery (including human review time).

---

### 9.4 Example 3: Rollback PR with CI Failure

**Scenario:** Rollback PR fails CI checks.

**Steps:**

1-4. **Same as Example 2** (detection, persistence, candidate, PR creation)

5. **CI pipeline runs:**
   - Unit tests: ❌ **Failed** (5 test failures)
   - CI status: `failure`

6. **AI Operator checks invariants:**
   - I6_ci_success: ❌ **FAIL** (CI failed)
   - Auto-merge blocked

7. **AI Operator escalates:**
   - Creates GitHub issue: `[URGENT] AI Operator Escalation: payment-service - CI Failure on Rollback PR`
   - FSM transitions to `Abort`
   - Alerts senior maintainer

8. **Senior Maintainer investigates:**
   - Reviews test failures
   - Realizes rollback target also has bug
   - Manually creates alternative rollback to older revision

**Result:** AI Operator correctly aborted unsafe rollback, human intervened.

---

### 9.5 Example 4: Health Restores Before Merge

**Scenario:** Degradation is transient, recovers before merge.

**Steps:**

1-4. **Same as Example 2** (detection, persistence, candidate, PR creation)

5. **CI pipeline runs:**
   - Time: 10:33:00 - CI complete, all passed

6. **AI Operator re-checks health before merge:**
   - I2_health_degraded: ❌ **FAIL** (health now `Healthy`)
   - Auto-merge blocked

7. **AI Operator closes PR:**
   - Adds comment: "Health restored before merge, rollback no longer needed"
   - Closes PR #42
   - FSM transitions to `Idle`

**Result:** Unnecessary rollback avoided, no changes merged.

---

## 10. Troubleshooting

### 10.1 Common Issues

| Issue | Symptoms | Diagnosis | Resolution |
|-------|----------|-----------|------------|
| **AI Operator not creating rollback PR** | Application degraded, no PR created | Check FSM state: stuck in `DegradationDetected`? | Verify candidate search didn't timeout; check Git history has stable commits |
| **Rollback PR not auto-merging (staging)** | PR created, CI passed, but no merge | Check audit logs for invariant results | One invariant failed (e.g., health restored, CI pending); review invariant checks |
| **CI failing on rollback PR** | CI status: `failure` | Review CI logs | Rollback target may also have bugs; manually select different candidate |
| **FSM stuck in Abort state** | No new rollbacks being created | Check correlation ID in etcd | Reset FSM manually: `curl -X POST http://ai-operator:8080/reset/{correlationId}` |
| **Duplicate rollback PRs** | Multiple PRs for same app | Check GitHub labels | Close duplicates, investigate invariant I7 enforcement |

### 10.2 Debug Commands

**Check AI Operator status:**
```bash
kubectl -n ai-operator-system get pods
kubectl -n ai-operator-system logs ai-operator-<pod-id>
```

**Query etcd for active rollbacks:**
```bash
etcdctl get /ai-operator/rollback/ --prefix
```

**Query audit logs:**
```bash
# Elasticsearch query
GET ai-operator-audit-2026.02.27/_search
{
  "query": {
    "match": {
      "correlationId": "uuid-1234"
    }
  }
}
```

**Check Prometheus metrics:**
```bash
# Active rollbacks
ai_operator_active_rollbacks{environment="staging"}

# Abort rate
rate(ai_operator_rollbacks_aborted_total[1h])
```

---

## Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | (Original) | - | Initial full context specification |
| 2.0 | 2026-02-27 | AI DevOps Team | Reconciled timing (3 checks, not 10 min), added role hierarchy, CI config, worked examples |

---

**Related Documents:**
- [Architecture](./ai-operator-architecture.md) - System overview and glossary
- [MCP API Specification](./ai-operator-mcp-api-spec.md) - MCP call details
- [Governance Specification](./ai-operator-governance-spec.md) - Invariants and safety rules
- [Rollback Engine](./ai-rollback-engine-spec.md) - Technical implementation
- [Deployment Guide](./ai-operator-deployment-guide.md) - Installation and operations
