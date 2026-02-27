# AI Operator Deployment Guide

**Version:** 1.0
**Last Updated:** 2026-02-27
**Status:** Production-Ready Installation Guide
**Audience:** DevOps Engineers, SREs, Platform Engineers

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Architecture Overview](#2-architecture-overview)
3. [Installation](#3-installation)
4. [Configuration](#4-configuration)
5. [Kubernetes Manifests](#5-kubernetes-manifests)
6. [Observability Setup](#6-observability-setup)
7. [Runbooks](#7-runbooks)
8. [Maintenance](#8-maintenance)

---

## 1. Prerequisites

### 1.1 Required Infrastructure

| Component | Version | Purpose |
|-----------|---------|---------|
| **Kubernetes** | 1.28+ | Deployment platform |
| **Argo CD** | 2.8+ | GitOps continuous deployment |
| **NATS JetStream** | 2.10+ | Event bus for module communication |
| **etcd** | 3.5+ | State persistence |
| **Prometheus** | 2.45+ | Metrics collection |
| **Elasticsearch** | 8.x | Audit log storage |
| **GitHub** | Enterprise or Cloud | Git repository hosting |

### 1.2 Required Credentials

| Credential | Type | Permissions |
|------------|------|-------------|
| **GitHub App Token** | OAuth token | `contents:read`, `pull_requests:write`, `checks:read` |
| **Kubernetes ServiceAccount** | K8s SA | `get/list` on deployments/pods (namespace-scoped) |
| **Argo CD API Token** | Bearer token | `applications:get` (read-only) |
| **Prometheus** | Internal | (Usually no auth required for internal cluster access) |
| **Elasticsearch** | API key | Write access to `ai-operator-audit-*` indices |

### 1.3 Network Requirements

| Source | Destination | Port | Protocol | Purpose |
|--------|-------------|------|----------|---------|
| AI Operator Pod | GitHub API | 443 | HTTPS | MCP calls |
| AI Operator Pod | Kubernetes API | 6443 | HTTPS | Deployment status |
| AI Operator Pod | Argo CD API | 443 | HTTPS | Health checks |
| AI Operator Pod | NATS | 4222 | NATS | Event bus |
| AI Operator Pod | etcd | 2379 | gRPC | State storage |
| AI Operator Pod | Prometheus | 9090 | HTTP | Metrics query |
| AI Operator Pod | Elasticsearch | 9200 | HTTP | Audit logs |

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                  Kubernetes Cluster (ai-operator-system)         │
├─────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌───────────────────────────────────────────────────────┐      │
│  │              AI Operator Pod                          │      │
│  │                                                       │      │
│  │  ┌──────────────┐   ┌──────────────┐                │      │
│  │  │ FSM Engine   │   │   Modules    │                │      │
│  │  │              │   │ - Degradation│                │      │
│  │  │              │   │ - Stability  │                │      │
│  │  │              │   │ - Candidate  │                │      │
│  │  │              │   │ - PR Gen     │                │      │
│  │  │              │   │ - Merge      │                │      │
│  │  └──────────────┘   └──────────────┘                │      │
│  │                                                       │      │
│  │  Environment Variables:                               │      │
│  │  - GITHUB_TOKEN (from secret)                        │      │
│  │  - ARGOCD_TOKEN (from secret)                        │      │
│  │  - NATS_URL, ETCD_ENDPOINTS, etc.                   │      │
│  └───────────────────────────────────────────────────────┘      │
│                           │                                      │
│                           ▼                                      │
│  ┌───────────────────────────────────────────────────────┐      │
│  │              NATS StatefulSet (3 replicas)            │      │
│  │  - Event bus for module communication                 │      │
│  │  - Persistent volumes for message retention           │      │
│  └───────────────────────────────────────────────────────┘      │
│                           │                                      │
│                           ▼                                      │
│  ┌───────────────────────────────────────────────────────┐      │
│  │              etcd StatefulSet (3 replicas)            │      │
│  │  - State persistence for FSM                          │      │
│  │  - Persistent volumes for durability                  │      │
│  └───────────────────────────────────────────────────────┘      │
│                                                                   │
└─────────────────────────────────────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
        ▼                  ▼                  ▼
   GitHub API      Argo CD API      Prometheus
   (External)      (In-cluster)     (In-cluster)
```

---

## 3. Installation

### 3.1 Create Namespace

```bash
kubectl create namespace ai-operator-system
```

### 3.2 Install NATS JetStream

```bash
# Add NATS Helm repo
helm repo add nats https://nats-io.github.io/k8s/helm/charts/
helm repo update

# Install NATS with JetStream
helm install nats nats/nats \
  --namespace ai-operator-system \
  --set jetstream.enabled=true \
  --set jetstream.fileStorage.size=10Gi \
  --set replicaCount=3
```

### 3.3 Install etcd

```bash
# Add Bitnami Helm repo
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo update

# Install etcd
helm install etcd bitnami/etcd \
  --namespace ai-operator-system \
  --set replicaCount=3 \
  --set persistence.size=10Gi \
  --set auth.rbac.enabled=false
```

### 3.4 Create Secrets

**GitHub Token:**
```bash
kubectl -n ai-operator-system create secret generic ai-operator-secrets \
  --from-literal=github-token=ghp_YOUR_GITHUB_APP_TOKEN \
  --from-literal=argocd-token=YOUR_ARGOCD_API_TOKEN \
  --from-literal=elasticsearch-api-key=YOUR_ES_API_KEY
```

### 3.5 Install AI Operator

```bash
# Apply all manifests (see Section 5 for full manifests)
kubectl apply -f manifests/
```

---

## 4. Configuration

### 4.1 Environment Variables

Configure via ConfigMap or environment variables in Deployment:

| Variable | Default | Description |
|----------|---------|-------------|
| `POLL_INTERVAL_SEC` | `10` | Health check polling interval (seconds) |
| `DEGRADATION_PERSISTENCE_CHECKS` | `3` | Number of consecutive degraded checks required |
| `HEALTH_RESTORATION_DURATION` | `60` | Seconds to confirm health restored |
| `CANDIDATE_SEARCH_TIMEOUT` | `120` | Max time to find rollback candidate (seconds) |
| `CI_WAIT_TIMEOUT` | `300` | Max wait for CI completion (seconds) |
| `MERGE_APPROVAL_TIMEOUT` | `3600` | Max wait for human approval in production (seconds) |
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARN, ERROR) |
| `NATS_URL` | `nats://nats:4222` | NATS server URL |
| `ETCD_ENDPOINTS` | `etcd:2379` | etcd endpoints (comma-separated) |
| `PROMETHEUS_URL` | `http://prometheus:9090` | Prometheus server URL |
| `ELASTICSEARCH_URL` | `http://elasticsearch:9200` | Elasticsearch server URL |
| `GITHUB_ORG` | (required) | GitHub organization name |
| `MONITORED_APPS` | (required) | Comma-separated list of Argo CD app names to monitor |

**Example ConfigMap:**
```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: ai-operator-config
  namespace: ai-operator-system
data:
  POLL_INTERVAL_SEC: "10"
  DEGRADATION_PERSISTENCE_CHECKS: "3"
  LOG_LEVEL: "INFO"
  GITHUB_ORG: "my-org"
  MONITORED_APPS: "payment-service,user-service,order-service"
```

### 4.2 Monitored Applications

**Via ConfigMap:**
```yaml
MONITORED_APPS: "payment-service,user-service,order-service"
```

**Via Argo CD Labels:**
Alternatively, monitor all apps with label `ai-operator.enabled=true`:
```yaml
# In Argo CD Application manifest
metadata:
  labels:
    ai-operator.enabled: "true"
    environment: "staging"  # or "production"
```

---

## 5. Kubernetes Manifests

### 5.1 ServiceAccount and RBAC

**File:** `manifests/rbac.yaml`

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: ai-operator
  namespace: ai-operator-system
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: ai-operator-reader
rules:
  # Deployments (read-only)
  - apiGroups: ["apps"]
    resources: ["deployments"]
    verbs: ["get", "list"]

  # Pods (read-only)
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list"]

  # Namespaces (read metadata)
  - apiGroups: [""]
    resources: ["namespaces"]
    verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: ai-operator-reader-binding
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: ai-operator-reader
subjects:
  - kind: ServiceAccount
    name: ai-operator
    namespace: ai-operator-system
```

### 5.2 Deployment

**File:** `manifests/deployment.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: ai-operator
  namespace: ai-operator-system
  labels:
    app: ai-operator
spec:
  replicas: 1  # Single instance with leader election
  strategy:
    type: Recreate  # Avoid multiple instances during updates
  selector:
    matchLabels:
      app: ai-operator
  template:
    metadata:
      labels:
        app: ai-operator
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/port: "8080"
        prometheus.io/path: "/metrics"
    spec:
      serviceAccountName: ai-operator
      containers:
        - name: operator
          image: my-registry/ai-operator:v1.0.0
          imagePullPolicy: IfNotPresent
          ports:
            - name: http
              containerPort: 8080
              protocol: TCP
          env:
            # Secrets
            - name: GITHUB_TOKEN
              valueFrom:
                secretKeyRef:
                  name: ai-operator-secrets
                  key: github-token
            - name: ARGOCD_TOKEN
              valueFrom:
                secretKeyRef:
                  name: ai-operator-secrets
                  key: argocd-token
            - name: ELASTICSEARCH_API_KEY
              valueFrom:
                secretKeyRef:
                  name: ai-operator-secrets
                  key: elasticsearch-api-key

            # Config from ConfigMap
            - name: POLL_INTERVAL_SEC
              valueFrom:
                configMapKeyRef:
                  name: ai-operator-config
                  key: POLL_INTERVAL_SEC
            - name: LOG_LEVEL
              valueFrom:
                configMapKeyRef:
                  name: ai-operator-config
                  key: LOG_LEVEL
            - name: GITHUB_ORG
              valueFrom:
                configMapKeyRef:
                  name: ai-operator-config
                  key: GITHUB_ORG
            - name: MONITORED_APPS
              valueFrom:
                configMapKeyRef:
                  name: ai-operator-config
                  key: MONITORED_APPS

            # Infrastructure endpoints
            - name: NATS_URL
              value: "nats://nats:4222"
            - name: ETCD_ENDPOINTS
              value: "etcd:2379"
            - name: PROMETHEUS_URL
              value: "http://prometheus:9090"
            - name: ELASTICSEARCH_URL
              value: "http://elasticsearch:9200"
            - name: ARGOCD_URL
              value: "https://argocd-server.argocd.svc.cluster.local"

          resources:
            requests:
              cpu: 200m
              memory: 512Mi
            limits:
              cpu: 500m
              memory: 1Gi

          livenessProbe:
            httpGet:
              path: /healthz
              port: 8080
            initialDelaySeconds: 30
            periodSeconds: 10
            timeoutSeconds: 5
            failureThreshold: 3

          readinessProbe:
            httpGet:
              path: /readyz
              port: 8080
            initialDelaySeconds: 10
            periodSeconds: 5
            timeoutSeconds: 3
            failureThreshold: 3
```

### 5.3 Service

**File:** `manifests/service.yaml`

```yaml
apiVersion: v1
kind: Service
metadata:
  name: ai-operator
  namespace: ai-operator-system
  labels:
    app: ai-operator
spec:
  selector:
    app: ai-operator
  ports:
    - name: http
      port: 8080
      targetPort: 8080
      protocol: TCP
  type: ClusterIP
```

---

## 6. Observability Setup

### 6.1 Prometheus Metrics

**ServiceMonitor (if using Prometheus Operator):**

**File:** `manifests/servicemonitor.yaml`

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: ai-operator
  namespace: ai-operator-system
  labels:
    app: ai-operator
spec:
  selector:
    matchLabels:
      app: ai-operator
  endpoints:
    - port: http
      path: /metrics
      interval: 30s
```

**Key Metrics to Monitor:**

```yaml
# Counters
ai_operator_degradations_detected_total{app_name, environment}
ai_operator_rollbacks_proposed_total{app_name, environment, success}
ai_operator_rollbacks_merged_total{app_name, environment, auto_merged}
ai_operator_rollbacks_aborted_total{app_name, environment, reason}
ai_operator_invariant_violations_total{invariant_id}
ai_operator_mcp_call_errors_total{call_name, error_code}

# Gauges
ai_operator_fsm_state{app_name, state}
ai_operator_active_rollbacks{environment}

# Histograms
ai_operator_mean_time_to_recovery_seconds{app_name, environment}
ai_operator_candidate_resolution_duration_seconds{app_name}
ai_operator_mcp_call_duration_seconds{call_name}
```

### 6.2 Grafana Dashboard

**Dashboard JSON:** `observability/grafana-dashboard.json`

**Panels:**
1. **Rollback Success Rate (last 24h)**
   - Query: `sum(rate(ai_operator_rollbacks_merged_total[24h])) / sum(rate(ai_operator_rollbacks_proposed_total[24h]))`
   - Type: Single stat with gauge

2. **Active Rollback Attempts (by FSM state)**
   - Query: `ai_operator_fsm_state`
   - Type: Pie chart

3. **Mean Time to Recovery Trend**
   - Query: `histogram_quantile(0.95, ai_operator_mean_time_to_recovery_seconds)`
   - Type: Time series graph

4. **MCP Call Latency (p50, p95, p99)**
   - Query: `histogram_quantile(0.95, ai_operator_mcp_call_duration_seconds)`
   - Type: Time series graph

5. **Invariant Violations by Type**
   - Query: `sum by (invariant_id) (ai_operator_invariant_violations_total)`
   - Type: Bar chart

6. **Degradations Detected per App**
   - Query: `sum by (app_name) (ai_operator_degradations_detected_total)`
   - Type: Time series graph

### 6.3 Alerting Rules

**File:** `observability/prometheus-rules.yaml`

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: ai-operator-alerts
  namespace: ai-operator-system
spec:
  groups:
    - name: ai-operator
      interval: 30s
      rules:
        - alert: AIOperatorHighAbortRate
          expr: rate(ai_operator_rollbacks_aborted_total[1h]) > 2
          for: 5m
          labels:
            severity: warning
          annotations:
            summary: "AI Operator abort rate is high"
            description: "More than 2 rollback aborts per hour in the last 5 minutes"

        - alert: AIOperatorSlowRecovery
          expr: histogram_quantile(0.95, ai_operator_mean_time_to_recovery_seconds) > 600
          for: 10m
          labels:
            severity: warning
          annotations:
            summary: "AI Operator recovery time is slow"
            description: "P95 recovery time exceeds 10 minutes"

        - alert: AIOperatorCIFailures
          expr: sum(ai_operator_invariant_violations_total{invariant_id="I6_ci_success"}) > 5
          for: 1h
          labels:
            severity: warning
          annotations:
            summary: "High CI failure rate on rollback PRs"
            description: "More than 5 CI failures in the last hour"

        - alert: AIOperatorPermissionDenied
          expr: ai_operator_mcp_call_errors_total{error_code="PERMISSION_DENIED"} > 0
          for: 1m
          labels:
            severity: critical
          annotations:
            summary: "AI Operator has permission issues"
            description: "PERMISSION_DENIED error detected - check service account credentials"

        - alert: AIOperatorDown
          expr: up{job="ai-operator"} == 0
          for: 5m
          labels:
            severity: critical
          annotations:
            summary: "AI Operator is down"
            description: "AI Operator pod is not responding"
```

### 6.4 Logging Configuration

**Structured JSON Logs:**
- All logs output to stdout in JSON format
- Captured by Kubernetes logging infrastructure (Fluentd, Fluent Bit, etc.)
- Forwarded to Elasticsearch

**Log Levels:**
- `DEBUG`: Module-level operations, MCP call details
- `INFO`: FSM transitions, successful operations
- `WARN`: Invariant violations, retries
- `ERROR`: MCP failures, FSM aborts

**Example Log Entry:**
```json
{
  "timestamp": "2026-02-27T10:30:00.123Z",
  "level": "INFO",
  "module": "MergeController",
  "correlationId": "uuid-1234",
  "message": "Auto-merging rollback PR in staging",
  "metadata": {
    "appName": "payment-service",
    "prNumber": 42,
    "environment": "staging"
  }
}
```

---

## 7. Runbooks

### 7.1 Runbook: AI Operator in Abort State

**Symptoms:**
- FSM stuck in `Abort` state for specific application
- No new rollback PRs being created despite continued degradation
- Alert: "AIOperatorHighAbortRate"

**Investigation:**

1. **Check Recent Audit Logs:**
   ```bash
   kubectl -n ai-operator-system logs ai-operator-<pod-id> --since=1h | grep Abort
   ```

2. **Identify Correlation ID and Failure Reason:**
   ```bash
   # From audit logs, extract:
   # - correlationId: uuid-1234
   # - reason: CI_FAILURE, PERMISSION_DENIED, etc.
   ```

3. **Check etcd State:**
   ```bash
   kubectl exec -n ai-operator-system etcd-0 -- etcdctl get /ai-operator/rollback/payment-service/uuid-1234/state
   ```

**Common Causes and Resolutions:**

| Cause | Symptoms | Resolution |
|-------|----------|------------|
| **MCP Permission Denied** | `error_code=PERMISSION_DENIED` in logs | Verify GitHub App token has correct permissions; regenerate if expired |
| **Git Conflict in Rollback PR** | `error_code=CONFLICT` in logs | Manually resolve conflict in PR or close and recreate |
| **All Candidates Failed CI** | `reason=NO_STABLE_CANDIDATE` | Investigate why no stable revisions exist; may need manual intervention |
| **Branch Protection Violation** | `invariant_id=I8_branch_protection FAIL` | Check branch protection rules; ensure AI operator is allowed to merge |

**Resolution Steps:**

1. **Fix Underlying Issue:**
   - Example: Restore GitHub permissions, resolve Git conflict

2. **Manually Reset FSM:**
   ```bash
   curl -X POST http://ai-operator.ai-operator-system.svc.cluster.local:8080/reset/uuid-1234
   ```

3. **Monitor for Successful Transition:**
   ```bash
   kubectl -n ai-operator-system logs -f ai-operator-<pod-id>
   # Should see: FSM transition Abort → Idle
   ```

**Prevention:**
- Ensure service account has correct RBAC permissions
- Keep branch protection rules compatible with AI Operator
- Maintain adequate test coverage for CI

---

### 7.2 Runbook: Rollback PR Not Auto-Merging (Staging)

**Symptoms:**
- Rollback PR created
- CI passed
- Environment is staging
- But PR not merged (still open)

**Investigation:**

1. **Check Invariant Results in Audit Log:**
   ```bash
   kubectl -n ai-operator-system logs ai-operator-<pod-id> | \
     jq 'select(.actionType=="INVARIANT_CHECK" and .correlationId=="uuid-1234")'
   ```

2. **Identify Failed Invariant:**
   ```json
   {
     "invariantsChecked": {
       "I1_environment": "PASS",
       "I2_health_degraded": "FAIL",  # <-- Health restored!
       ...
     }
   }
   ```

**Common Invariant Failures:**

| Invariant | Failure Reason | Resolution |
|-----------|---------------|------------|
| **I2_health_degraded** | Health restored before merge | Expected behavior - PR will be closed automatically |
| **I6_ci_success** | CI still pending or failed | Wait for CI or investigate test failures |
| **I7_no_conflicts** | Multiple rollback PRs exist | Close duplicate PRs manually |
| **I8_branch_protection** | PR not mergeable (conflict) | Resolve merge conflict in PR |

**Resolution:**
- If health restored: PR will auto-close (no action needed)
- If CI pending: Wait or investigate CI logs
- If invariant legitimately failing: Fix underlying issue

---

### 7.3 Runbook: AI Operator Pod Crash Loop

**Symptoms:**
- Pod status: `CrashLoopBackOff`
- Alert: "AIOperatorDown"

**Investigation:**

1. **Check Pod Logs:**
   ```bash
   kubectl -n ai-operator-system logs ai-operator-<pod-id> --previous
   ```

2. **Check Pod Events:**
   ```bash
   kubectl -n ai-operator-system describe pod ai-operator-<pod-id>
   ```

**Common Causes:**

| Error Message | Cause | Resolution |
|---------------|-------|------------|
| `connection refused: nats:4222` | NATS not running | Check NATS pods: `kubectl -n ai-operator-system get pods -l app=nats` |
| `connection refused: etcd:2379` | etcd not running | Check etcd pods: `kubectl -n ai-operator-system get pods -l app=etcd` |
| `401 Unauthorized: GitHub API` | Invalid GitHub token | Verify secret: `kubectl -n ai-operator-system get secret ai-operator-secrets -o yaml` |
| `OOMKilled` | Out of memory | Increase memory limits in Deployment |

**Resolution:**
1. Fix underlying infrastructure issue (restart NATS/etcd if needed)
2. Verify secrets are correct
3. Increase resource limits if needed
4. Restart AI Operator: `kubectl -n ai-operator-system rollout restart deployment ai-operator`

---

### 7.4 Runbook: High Degradation Detection Rate

**Symptoms:**
- Alert: "AIOperatorHighAbortRate"
- Many degradations detected across applications
- Possibly legitimate application issues OR flapping health checks

**Investigation:**

1. **Check Degradation Metrics:**
   ```promql
   rate(ai_operator_degradations_detected_total[1h])
   ```

2. **Identify Affected Applications:**
   ```promql
   sum by (app_name) (ai_operator_degradations_detected_total)
   ```

3. **Check Argo CD Health Directly:**
   ```bash
   argocd app get payment-service --output json | jq '.status.health'
   ```

**Diagnosis:**

| Pattern | Likely Cause | Action |
|---------|-------------|--------|
| Single app degraded repeatedly | Legitimate app issue | Investigate app logs, metrics |
| All apps flapping Healthy ↔ Degraded | Argo CD or K8s API instability | Check cluster health |
| Degradations correlate with deployments | Breaking changes in deployments | Review recent PRs |

**Resolution:**
- If legitimate app issues: Fix applications
- If infrastructure flapping: Increase `DEGRADATION_PERSISTENCE_CHECKS` temporarily
- If breaking deployments: Improve pre-deployment testing

---

## 8. Maintenance

### 8.1 Upgrading AI Operator

1. **Review Changelog:**
   - Check release notes for breaking changes

2. **Update Image:**
   ```bash
   kubectl -n ai-operator-system set image deployment/ai-operator \
     operator=my-registry/ai-operator:v1.1.0
   ```

3. **Monitor Rollout:**
   ```bash
   kubectl -n ai-operator-system rollout status deployment/ai-operator
   ```

4. **Verify State Recovery:**
   - AI Operator should resume any in-progress rollbacks from etcd

### 8.2 Backup and Recovery

**etcd Backup:**
```bash
kubectl exec -n ai-operator-system etcd-0 -- etcdctl snapshot save /tmp/etcd-backup.db
kubectl cp ai-operator-system/etcd-0:/tmp/etcd-backup.db ./etcd-backup.db
```

**etcd Restore:**
```bash
kubectl cp ./etcd-backup.db ai-operator-system/etcd-0:/tmp/etcd-backup.db
kubectl exec -n ai-operator-system etcd-0 -- etcdctl snapshot restore /tmp/etcd-backup.db
```

**NATS Backup:**
- NATS messages are ephemeral (7-day retention)
- No backup needed (state is in etcd)

### 8.3 Rotating Credentials

**GitHub Token:**
```bash
# Generate new token in GitHub
# Update secret
kubectl -n ai-operator-system create secret generic ai-operator-secrets \
  --from-literal=github-token=NEW_TOKEN \
  --dry-run=client -o yaml | kubectl apply -f -

# Restart operator to pick up new secret
kubectl -n ai-operator-system rollout restart deployment/ai-operator
```

**Argo CD Token:**
```bash
# Similar process as GitHub token
kubectl -n ai-operator-system create secret generic ai-operator-secrets \
  --from-literal=argocd-token=NEW_TOKEN \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n ai-operator-system rollout restart deployment/ai-operator
```

### 8.4 Scaling Considerations

**Current Design:**
- Single replica with leader election
- Handles up to ~100 monitored applications per instance

**Scaling Beyond 100 Apps:**
- Deploy multiple AI Operator instances with different `MONITORED_APPS`
- Use separate NATS/etcd clusters per instance (isolation)

**High Availability:**
- Enable leader election (requires etcd lease)
- Run 3 replicas with leader election enabled

---

## Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-02-27 | AI DevOps Team | Initial deployment guide |

---

**Related Documents:**
- [Architecture](./ai-operator-architecture.md) - System overview
- [Governance Specification](./ai-operator-governance-spec.md) - Security and compliance
- [Rollback Engine](./ai-rollback-engine-spec.md) - Technical implementation
- [Full Context](./ai-operator-full-context.md) - Operational rules
