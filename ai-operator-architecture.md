# AI DevOps Operator - Master Architecture Document

**Version:** 1.0
**Last Updated:** 2026-02-27
**Status:** Production-Ready Specification

---

## Table of Contents

1. [System Overview](#system-overview)
2. [High-Level Architecture](#high-level-architecture)
3. [Component Relationships](#component-relationships)
4. [Document Hierarchy](#document-hierarchy)
5. [Key Design Principles](#key-design-principles)
6. [Technology Stack](#technology-stack)
7. [Glossary](#glossary)

---

## 1. System Overview

### Purpose

The AI DevOps Operator is an autonomous system that detects degraded application deployments in Kubernetes environments managed by Argo CD and automatically creates rollback pull requests to restore service health. Operating at **Level 2 Autonomy**, it can automatically merge rollback PRs in staging environments under strict safety conditions, while requiring human approval for production rollbacks.

### Scope

**In Scope:**
- Automated health monitoring of Argo CD applications
- Detection of persistent degradation states (3 consecutive health checks)
- Selection of stable rollback candidates from Git history
- Automated creation of rollback pull requests
- Autonomous merge in staging environments (under strict invariants)
- Human-in-the-loop approval for production environments
- Comprehensive audit logging of all actions
- State machine-driven workflow with failure recovery

**Out of Scope:**
- Application promotion (staging → production)
- Initial deployment of new applications
- Infrastructure provisioning
- Application configuration changes (non-rollback)
- Blue-green or canary deployment strategies
- Performance optimization recommendations

### Key Capabilities

| Capability | Description | Autonomy Level |
|------------|-------------|----------------|
| Degradation Detection | Monitors Argo CD health status every 10 seconds | Fully Automated |
| Rollback Candidate Selection | Identifies last known-good version from Git + metrics | Fully Automated |
| PR Creation | Generates rollback PR with detailed context | Fully Automated |
| Auto-Merge (Staging) | Merges PR if all 8 invariants pass | Level 2 Autonomy |
| Auto-Merge (Production) | Requires human approval | Human-in-Loop |
| Health Verification | Validates successful recovery post-rollback | Fully Automated |

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                       AI DevOps Operator System                      │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌────────────────────┐         ┌─────────────────────────┐         │
│  │   FSM Controller   │◄────────┤   Event Bus (NATS)      │         │
│  │  (State Machine)   │         │  - degradation.detected │         │
│  └────────────────────┘         │  - candidate.resolved   │         │
│           │                     │  - pr.created           │         │
│           ▼                     └─────────────────────────┘         │
│  ┌─────────────────────────────────────────────────┐               │
│  │           Processing Modules                     │               │
│  ├─────────────────────────────────────────────────┤               │
│  │  1. DegradationDetector    (polls Argo CD)      │               │
│  │  2. StabilityAnalyzer       (verifies persistence) │            │
│  │  3. RollbackCandidateResolver (queries Git+metrics) │          │
│  │  4. PRGenerator             (creates GitHub PR)  │               │
│  │  5. MergeController         (enforces invariants) │              │
│  │  6. HealthMonitor           (post-rollback check) │              │
│  └─────────────────────────────────────────────────┘               │
│           │                                                          │
│           ▼                                                          │
│  ┌─────────────────────────────────────────────────┐               │
│  │   State Store (etcd)                            │               │
│  │   - FSM states per rollback attempt             │               │
│  │   - Correlation IDs and metadata                │               │
│  │   - 7-day retention for audit                   │               │
│  └─────────────────────────────────────────────────┘               │
│                                                                       │
└───────────────────────────┬───────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│   GitHub     │   │  Kubernetes  │   │   Argo CD    │
│   (via MCP)  │   │  (via MCP)   │   │  (via MCP)   │
├──────────────┤   ├──────────────┤   ├──────────────┤
│ - Read repo  │   │ - Get deploy │   │ - Get health │
│ - Create PR  │   │ - List pods  │   │ - Get sync   │
│ - Merge PR   │   │ - Get status │   │   status     │
│ - Get CI     │   │              │   │              │
└──────────────┘   └──────────────┘   └──────────────┘
```

### Data Flow

1. **Detection Phase:** DegradationDetector polls Argo CD every 10s → publishes `degradation.detected` event
2. **Validation Phase:** StabilityAnalyzer verifies persistence (3 checks) → publishes `degradation.confirmed`
3. **Resolution Phase:** RollbackCandidateResolver queries Git + metrics → publishes `candidate.resolved`
4. **PR Creation Phase:** PRGenerator creates GitHub PR → publishes `pr.created`
5. **Merge Phase:** MergeController waits for CI, checks invariants → merges (staging) or alerts (production)
6. **Verification Phase:** HealthMonitor validates recovery → transitions FSM to `RollbackComplete`

---

## 3. Component Relationships

### Module Dependencies

```
DegradationDetector
    ├─► Argo CD MCP (getApplicationHealth)
    ├─► Kubernetes MCP (getDeploymentStatus)
    └─► Event Bus (publishes degradation.detected)

StabilityAnalyzer
    ├─► Event Bus (subscribes degradation.detected)
    ├─► State Store (reads health check history)
    └─► Event Bus (publishes degradation.confirmed)

RollbackCandidateResolver
    ├─► Event Bus (subscribes degradation.confirmed)
    ├─► GitHub MCP (readRepositoryContent - for Git log)
    ├─► Metrics Backend (queries historical uptime)
    └─► Event Bus (publishes candidate.resolved)

PRGenerator
    ├─► Event Bus (subscribes candidate.resolved)
    ├─► GitHub MCP (createPullRequest)
    └─► Event Bus (publishes pr.created)

MergeController
    ├─► Event Bus (subscribes pr.created)
    ├─► GitHub MCP (getPullRequestStatus, mergePullRequest)
    ├─► Governance Engine (checks invariants)
    └─► State Store (updates FSM state)

HealthMonitor
    ├─► Argo CD MCP (getApplicationHealth)
    ├─► Kubernetes MCP (getDeploymentStatus)
    └─► FSM Controller (triggers state transitions)
```

### External Integrations

| System | Protocol | Purpose | Credentials |
|--------|----------|---------|-------------|
| GitHub | HTTPS + MCP | PR creation, merge, CI status | GitHub App Token (secret) |
| Kubernetes | K8s API + MCP | Pod/deployment status | Service Account |
| Argo CD | HTTP API + MCP | Application health | API Token (secret) |
| NATS | NATS Protocol | Event bus for module communication | Internal |
| etcd | gRPC | State persistence | Internal |
| Prometheus | HTTP API | Historical metrics | Internal |
| Elasticsearch | HTTP API | Audit log storage | Internal |

---

## 4. Document Hierarchy

This architecture document serves as the **master reference** for the AI DevOps Operator system. All other documents are subordinate and provide detailed specifications for specific aspects.

### Document Map

```
ai-operator-architecture.md (THIS DOCUMENT - START HERE)
    │
    ├─► ai-operator-mcp-api-spec.md
    │   │   Complete MCP API schemas (GitHub, Kubernetes, Argo CD)
    │   │   Referenced by: Governance Spec, Rollback Engine Spec
    │   │
    ├─► ai-operator-governance-spec.md
    │   │   Behavioral rules, invariants, RBAC, audit logging
    │   │   Referenced by: Rollback Engine Spec, Deployment Guide
    │   │
    ├─► ai-rollback-engine-spec.md
    │   │   FSM implementation, event schemas, algorithms
    │   │   References: MCP API Spec, Governance Spec
    │   │
    ├─► ai-operator-full-context.md
    │   │   Operational rules, acceptance criteria, worked examples
    │   │   References: All above documents
    │   │
    └─► ai-operator-deployment-guide.md
        │   K8s manifests, observability, runbooks
        │   References: Architecture, Governance Spec
```

### Document Roles and Audiences

| Document | Primary Audience | Purpose | Read First? |
|----------|-----------------|---------|-------------|
| `ai-operator-architecture.md` | All stakeholders | System overview, context, glossary | **YES** |
| `ai-operator-mcp-api-spec.md` | Developers, Integration Engineers | MCP contract details | After Architecture |
| `ai-operator-governance-spec.md` | Security, Compliance, Architects | Safety rules, audit requirements | After Architecture |
| `ai-rollback-engine-spec.md` | Developers, QA | Implementation details, FSM logic | After MCP + Governance |
| `ai-operator-full-context.md` | Operators, SREs | Day-to-day operation, examples | After Rollback Engine |
| `ai-operator-deployment-guide.md` | DevOps, SREs | Installation, configuration, troubleshooting | Before deployment |

### Cross-Reference Guidelines

- **When citing timing values:** Reference this document's Glossary (single source of truth)
- **When citing MCP calls:** Reference `ai-operator-mcp-api-spec.md` section numbers
- **When citing invariants:** Reference `ai-operator-governance-spec.md` section 4.1
- **When citing FSM states:** Reference `ai-rollback-engine-spec.md` section 3.1

---

## 5. Key Design Principles

### 1. Safety First
- **Staging-Only Auto-Merge:** Level 2 autonomy only applies to staging environments
- **Invariant Enforcement:** All 8 invariants must pass for auto-merge
- **Fail-Safe Default:** Any error → abort and alert humans
- **Immutable Audit Log:** All actions logged to append-only storage

### 2. Idempotency
- **Duplicate PR Prevention:** Check for existing open PRs before creating new ones
- **State Recovery:** FSM can resume from saved state after crashes
- **Retry Safety:** All MCP calls can be safely retried

### 3. Observability
- **Structured Logging:** JSON logs with correlation IDs
- **Rich Metrics:** Prometheus metrics for all key operations
- **Audit Trail:** Every action logged with reasoning and invariant checks
- **Distributed Tracing:** OpenTelemetry spans for end-to-end visibility

### 4. Modularity
- **Event-Driven:** Modules communicate via event bus
- **Loose Coupling:** Each module has single responsibility
- **Testability:** Modules can be tested in isolation with mocked events

### 5. Human-in-the-Loop
- **Production Approval Required:** No auto-merge in production
- **Transparent Reasoning:** All decisions explained in PR descriptions
- **Escalation Paths:** Clear procedures when AI encounters edge cases
- **Override Capability:** Humans can abort or take over at any time

---

## 6. Technology Stack

### Core Components

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| **Runtime** | Python | 3.11+ | Application logic |
| **State Machine** | python-statemachine | 2.x | FSM implementation |
| **Event Bus** | NATS | 2.10+ | Module communication |
| **State Store** | etcd | 3.5+ | FSM state persistence |
| **Metrics** | Prometheus | 2.45+ | Observability |
| **Logging** | Elasticsearch | 8.x | Audit log storage |
| **Tracing** | OpenTelemetry | 1.x | Distributed tracing |
| **Container Runtime** | Docker | 24.x | Containerization |
| **Orchestration** | Kubernetes | 1.28+ | Deployment platform |

### MCP Integrations

| MCP Provider | Version | API Surface |
|--------------|---------|-------------|
| GitHub MCP | 1.0 | `readRepositoryContent`, `createPullRequest`, `mergePullRequest`, `getPullRequestStatus`, `listOpenPullRequests` |
| Kubernetes MCP | 1.0 | `getDeploymentStatus`, `listPods` |
| Argo CD MCP | 1.0 | `getApplicationHealth`, `getApplicationSyncStatus` |

### External Dependencies

| Service | Required For | Fallback Strategy |
|---------|--------------|-------------------|
| GitHub | PR creation/merge | None - critical dependency |
| Kubernetes | Deployment status | None - critical dependency |
| Argo CD | Health monitoring | None - critical dependency |
| Prometheus | Historical metrics | Degrade to CI-only candidate selection |
| NATS | Module communication | In-memory event bus (dev/test only) |
| etcd | State persistence | In-memory store (dev/test only) |
| Elasticsearch | Audit logs | Fallback to file-based logging |

---

## 7. Glossary

### Terms and Definitions

| Term | Definition | Usage Context |
|------|-----------|---------------|
| **Stable Version** | A Git revision that achieved 99% uptime over 24h when deployed, OR has passing CI when metrics unavailable | Rollback candidate selection |
| **Persistent Degradation** | Application health status remains `Degraded` for **3 consecutive health checks** at 10-second intervals (30 seconds total) | Degradation detection threshold |
| **Level 2 Autonomy** | AI can execute actions autonomously under strict conditions; requires human approval in production | Governance model |
| **Invariant** | A safety condition that must be true for auto-merge to proceed | Governance enforcement |
| **Correlation ID** | UUID tracking a single rollback attempt across all logs and events | Audit logging |
| **FSM State** | Current position in the rollback workflow state machine | State tracking |
| **Health Check** | Single query to Argo CD `getApplicationHealth` API | Monitoring |
| **Rollback Candidate** | Git revision eligible for rollback (older than current, stable, CI passed) | Candidate selection |
| **Auto-Merge Conditions** | All 8 invariants pass AND environment is staging | Merge decision |
| **Escalation** | Transition from autonomous operation to human intervention | Error handling |

### Timing Constants (Single Source of Truth)

| Constant | Value | Used In | Rationale |
|----------|-------|---------|-----------|
| `HEALTH_CHECK_INTERVAL` | 10 seconds | DegradationDetector | Balance between responsiveness and API load |
| `DEGRADATION_PERSISTENCE_CHECKS` | 3 checks | StabilityAnalyzer | 30s total (3 × 10s) filters transient issues |
| `HEALTH_RESTORATION_DURATION` | 60 seconds | HealthMonitor | Verify stable recovery post-rollback |
| `CANDIDATE_SEARCH_TIMEOUT` | 120 seconds | RollbackCandidateResolver | Max time to search Git history |
| `CI_WAIT_TIMEOUT` | 300 seconds | MergeController | Max wait for CI to complete |
| `MERGE_APPROVAL_TIMEOUT` | 3600 seconds | MergeController (production) | 1 hour for human approval |
| `MCP_RETRY_COUNT` | 3 retries | All modules | Handle transient network errors |
| `MCP_RETRY_BACKOFF` | Exponential (1s, 2s, 4s) | All modules | Progressive backoff |

### Environment Definitions

| Environment | Auto-Merge | Approval Required | Purpose |
|-------------|-----------|------------------|---------|
| `staging` | Yes (if invariants pass) | No | Pre-production testing, Level 2 autonomy |
| `production` | No | Yes (1 senior maintainer) | Live customer traffic |

### FSM States

| State | Meaning | Terminal? |
|-------|---------|-----------|
| `Idle` | No active rollback attempts | No |
| `DegradationDetected` | Degradation observed, verifying persistence | No |
| `CandidateResolved` | Rollback target identified | No |
| `PRCreated` | GitHub PR created, awaiting CI | No |
| `AwaitingMergeApproval` | CI passed, checking merge conditions | No |
| `RollbackMerged` | PR merged, monitoring health | No |
| `RollbackComplete` | Health restored, rollback succeeded | Yes |
| `Abort` | Unrecoverable error, human intervention required | Yes |

### Invariant IDs

| ID | Short Name | Critical? |
|----|-----------|-----------|
| `I1_environment` | Environment is staging | Yes |
| `I2_health_degraded` | Argo CD reports Degraded | Yes |
| `I3_replica_shortage` | Available < Desired replicas | Yes |
| `I4_persistence` | Degraded for 3 checks | Yes |
| `I5_stable_previous` | Target has 99% historical uptime | Yes |
| `I6_ci_success` | CI status is success | Yes |
| `I7_no_conflicts` | No other open PRs on branch | Yes |
| `I8_branch_protection` | PR is mergeable | Yes |

*All invariants are critical - failure of any single invariant prevents auto-merge.*

---

## Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-27 | AI DevOps Team | Initial production-ready specification |

---

**Navigation:**
- **Next:** Read [`ai-operator-mcp-api-spec.md`](./ai-operator-mcp-api-spec.md) for MCP API details
- **Implementation:** See [`ai-rollback-engine-spec.md`](./ai-rollback-engine-spec.md) for technical implementation
- **Deployment:** See [`ai-operator-deployment-guide.md`](./ai-operator-deployment-guide.md) for installation

---

**Questions or Issues?**
- Technical questions: See [`ai-rollback-engine-spec.md`](./ai-rollback-engine-spec.md)
- Governance questions: See [`ai-operator-governance-spec.md`](./ai-operator-governance-spec.md)
- Operational questions: See [`ai-operator-full-context.md`](./ai-operator-full-context.md)
