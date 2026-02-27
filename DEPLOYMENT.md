# AI Operator - Deployment Guide

## Quick Deploy (5 Minutes)

### 1. Build and Push Docker Image

```bash
# Build
docker build -t ghcr.io/your-org/ai-operator:0.1.0 .

# Push
docker push ghcr.io/your-org/ai-operator:0.1.0
```

### 2. Create Kubernetes Namespace

```bash
kubectl create namespace ai-operator-system
```

### 3. Create Secrets

```bash
# GitHub token (with repo and org:read scopes)
kubectl create secret generic ai-operator-secrets \
  --namespace ai-operator-system \
  --from-literal=github-token=ghp_YOUR_GITHUB_TOKEN \
  --from-literal=ai-token=sk-ant-YOUR_ANTHROPIC_TOKEN

# Optional: Argo CD token (if using Argo CD integration)
kubectl create secret generic ai-operator-secrets \
  --namespace ai-operator-system \
  --from-literal=argocd-token=YOUR_ARGOCD_TOKEN \
  --dry-run=client -o yaml | kubectl apply -f -
```

### 4. Install CRD

```bash
kubectl apply -f charts/ai-operator/templates/crd.yaml
```

### 5. Deploy Operator via Helm

```bash
helm install ai-operator ./charts/ai-operator \
  --namespace ai-operator-system \
  --set image.repository=ghcr.io/your-org/ai-operator \
  --set image.tag=0.1.0 \
  --set github.organization=your-github-org \
  --set github.repositories="{repo1,repo2,repo3}"
```

### 6. Apply a Reconciliation Rule

```bash
# Start with staging auto-merge
kubectl apply -f examples/staging-automerge-rule.yaml
```

### 7. Verify Deployment

```bash
# Check operator pod
kubectl -n ai-operator-system get pods

# Check CRD installation
kubectl get crd prreconciliationrules.aioperator.io

# Check rules
kubectl -n ai-operator-system get prrules

# View logs
kubectl -n ai-operator-system logs -f deployment/ai-operator
```

## Configuration Examples

### Helm Values (Production)

Create `values-prod.yaml`:

```yaml
image:
  repository: ghcr.io/your-org/ai-operator
  tag: "0.1.0"
  pullPolicy: Always

replicaCount: 1

resources:
  limits:
    cpu: 1000m
    memory: 1Gi
  requests:
    cpu: 200m
    memory: 256Mi

github:
  organization: "acme-corp"
  repositories:
    - "payment-service"
    - "user-service"
    - "inventory-service"
  tokenSecretName: github-token-sealed
  tokenSecretKey: token

ai:
  endpoint: "https://api.anthropic.com/v1/messages"
  model: "claude-3-5-sonnet-20241022"
  maxTokens: 1024
  temperature: 0.2
  tokenSecretName: ai-token-sealed
  tokenSecretKey: token

argocd:
  enabled: true
  url: "https://argocd.acme-corp.com"
  tokenSecretName: argocd-token-sealed
  tokenSecretKey: token

logging:
  level: "INFO"
  format: "json"

metrics:
  enabled: true
  port: 9090

podSecurityContext:
  runAsNonRoot: true
  runAsUser: 1000
  fsGroup: 1000
  seccompProfile:
    type: RuntimeDefault

securityContext:
  allowPrivilegeEscalation: false
  capabilities:
    drop:
      - ALL
  readOnlyRootFilesystem: true
```

Deploy:

```bash
helm upgrade --install ai-operator ./charts/ai-operator \
  --namespace ai-operator-system \
  --values values-prod.yaml
```

## Multiple Environment Setup

### Staging Environment

```yaml
# staging-automerge-rule.yaml
apiVersion: aioperator.io/v1alpha1
kind: PRReconciliationRule
metadata:
  name: staging-automerge
  namespace: ai-operator-system
spec:
  selector:
    labels:
      include: ["auto-merge", "staging"]
  instruction: |
    Auto-merge if CI passes, no conflicts, and Argo CD degraded.
    Close if Argo CD healthy.
  argocdEnabled: true
  argocdAppNamePattern: "{{repository}}-staging"
  reconciliationInterval: 30
  mergeMethod: "SQUASH"
```

### Production Environment

```yaml
# production-approval-rule.yaml
apiVersion: aioperator.io/v1alpha1
kind: PRReconciliationRule
metadata:
  name: production-approval
  namespace: ai-operator-system
spec:
  selector:
    labels:
      include: ["production"]
  instruction: |
    Production PRs require senior maintainer approval.
    Always escalate, never auto-merge.
  argocdEnabled: false
  reconciliationInterval: 60
```

Apply both:

```bash
kubectl apply -f staging-automerge-rule.yaml
kubectl apply -f production-approval-rule.yaml
```

## Monitoring

### Prometheus ServiceMonitor

```yaml
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: ai-operator
  namespace: ai-operator-system
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: ai-operator
  endpoints:
    - port: metrics
      interval: 30s
```

### Grafana Dashboard

Import dashboard JSON (create custom dashboard with these metrics):

- `ai_operator_rules_total`
- `ai_operator_prs_processed_total`
- `ai_operator_actions_total{action="merge"}`
- `ai_operator_actions_total{action="escalate"}`
- `ai_operator_errors_total`

## Troubleshooting

### Common Issues

**1. Pod CrashLoopBackOff**

```bash
# Check logs
kubectl -n ai-operator-system logs deployment/ai-operator

# Common causes:
# - Missing secrets
# - Invalid GitHub token
# - CRD not installed
```

**2. PRs Not Processing**

```bash
# Check rule status
kubectl describe prrule <rule-name> -n ai-operator-system

# Verify PR labels match selector
# Check operator logs for errors
```

**3. AI Errors**

```bash
# Check AI token validity
# Verify endpoint is correct
# Review prompt template syntax
```

### Debug Mode

Enable debug logging:

```bash
helm upgrade ai-operator ./charts/ai-operator \
  --namespace ai-operator-system \
  --reuse-values \
  --set logging.level=DEBUG
```

## Security Hardening

### 1. Use External Secrets

Install External Secrets Operator:

```bash
helm repo add external-secrets https://charts.external-secrets.io
helm install external-secrets external-secrets/external-secrets \
  --namespace external-secrets-system \
  --create-namespace
```

Create ExternalSecret:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: ai-operator-secrets
  namespace: ai-operator-system
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend
    kind: SecretStore
  target:
    name: ai-operator-secrets
  data:
    - secretKey: github-token
      remoteRef:
        key: secret/ai-operator/github-token
    - secretKey: ai-token
      remoteRef:
        key: secret/ai-operator/ai-token
```

### 2. Network Policies

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: ai-operator
  namespace: ai-operator-system
spec:
  podSelector:
    matchLabels:
      app.kubernetes.io/name: ai-operator
  policyTypes:
    - Ingress
    - Egress
  egress:
    # Allow DNS
    - to:
        - namespaceSelector:
            matchLabels:
              name: kube-system
      ports:
        - protocol: UDP
          port: 53
    # Allow Kubernetes API
    - to:
        - namespaceSelector: {}
      ports:
        - protocol: TCP
          port: 443
    # Allow GitHub API
    - to:
        - podSelector: {}
      ports:
        - protocol: TCP
          port: 443
```

### 3. Pod Security Standards

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: ai-operator-system
  labels:
    pod-security.kubernetes.io/enforce: restricted
    pod-security.kubernetes.io/audit: restricted
    pod-security.kubernetes.io/warn: restricted
```

## Upgrade Procedure

### 1. Update Image

```bash
helm upgrade ai-operator ./charts/ai-operator \
  --namespace ai-operator-system \
  --set image.tag=0.2.0 \
  --reuse-values
```

### 2. Update CRD (if changed)

```bash
kubectl apply -f charts/ai-operator/templates/crd.yaml
```

### 3. Verify

```bash
kubectl -n ai-operator-system rollout status deployment/ai-operator
kubectl -n ai-operator-system get pods
```

## Uninstall

```bash
# Delete rules first
kubectl delete prrules --all -n ai-operator-system

# Uninstall Helm chart
helm uninstall ai-operator -n ai-operator-system

# Delete CRD
kubectl delete crd prreconciliationrules.aioperator.io

# Delete namespace
kubectl delete namespace ai-operator-system
```

## Next Steps

1. Create rules for your repositories
2. Set up monitoring dashboards
3. Configure alerting
4. Document team workflows
5. Train team on rule creation

## Support

- **Issues**: [GitHub Issues](https://github.com/your-org/ai-operator/issues)
- **Documentation**: See [README.md](../README.md)
- **Examples**: See [examples/](../examples/)
