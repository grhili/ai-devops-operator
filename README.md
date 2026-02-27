# AI-Driven PR Reconciliation Operator

[![CI](https://github.com/grhili/ai-devops-operator/actions/workflows/ci.yml/badge.svg)](https://github.com/grhili/ai-devops-operator/actions/workflows/ci.yml)
[![Status](https://img.shields.io/badge/status-beta-yellow)](https://github.com/grhili/ai-devops-operator)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**A Kubernetes-native operator that uses AI to automatically process pull requests based on natural language instructions.**

## 🎯 Key Features

- **AI-Driven Decisions**: Use natural language prompts instead of hardcoded logic
- **Kubernetes-Native**: CRDs for configuration, no external database needed
- **GitOps-Friendly**: All configuration in YAML, version controlled
- **GraphQL-First**: Efficient GitHub integration via GraphQL API
- **Multi-Environment**: Different rules for staging, production, etc.
- **Extensible**: Easy to add new processing rules without code changes

## 🚀 Quick Start

### Prerequisites

- Kubernetes 1.28+
- Helm 3.0+
- GitHub Personal Access Token or GitHub App
- AI API Token (Anthropic Claude, OpenAI, etc.)
- Optional: Argo CD for health checks

### Installation

1. **Create namespace and secrets:**

```bash
kubectl create namespace ai-operator-system

kubectl create secret generic ai-operator-secrets \
  --namespace ai-operator-system \
  --from-literal=github-token=ghp_your_github_token \
  --from-literal=ai-token=sk-ant-your_anthropic_token
```

2. **Install Helm chart:**

```bash
helm install ai-operator ./charts/ai-operator \
  --namespace ai-operator-system \
  --set github.organization=your-org \
  --set github.repositories="{repo1,repo2,repo3}"
```

3. **Apply a reconciliation rule:**

```bash
kubectl apply -f examples/staging-automerge-rule.yaml
```

4. **Verify installation:**

```bash
kubectl -n ai-operator-system get pods
kubectl -n ai-operator-system get prrules
kubectl -n ai-operator-system logs -f deployment/ai-operator
```

## 📖 Usage

### Creating a PR Reconciliation Rule

Create a `PRReconciliationRule` CRD to define how PRs should be processed:

```yaml
apiVersion: aioperator.io/v1alpha1
kind: PRReconciliationRule
metadata:
  name: my-automerge-rule
  namespace: ai-operator-system
spec:
  # PR selector
  selector:
    labels:
      include:
        - "auto-merge"
        - "staging"
      exclude:
        - "do-not-merge"
    baseBranch: "main"

  # AI instruction (natural language)
  instruction: |
    You are reviewing a pull request. Decide what action to take.

    **Context:**
    - PR #{{pr.number}}: {{pr.title}}
    - CI Status: {{pr.ciStatus}}
    - Mergeable: {{pr.mergeable}}

    **Rules:**
    - Auto-merge if CI passed and mergeable
    - Wait if CI pending
    - Escalate if CI failed

    Return JSON: {"action": "merge|wait|escalate|close", "reason": "..."}

  # Settings
  reconciliationInterval: 30
  mergeMethod: "SQUASH"
```

Apply the rule:

```bash
kubectl apply -f my-rule.yaml
```

### Updating a Rule

Edit the rule directly:

```bash
kubectl edit prrule my-automerge-rule -n ai-operator-system
```

Changes take effect immediately (within the reconciliation interval).

### Viewing Rule Status

```bash
kubectl get prrules -n ai-operator-system
kubectl describe prrule my-automerge-rule -n ai-operator-system
```

## 📋 Examples

See the [`examples/`](./examples/) directory for complete examples:

- **[staging-automerge-rule.yaml](./examples/staging-automerge-rule.yaml)** - Auto-merge staging PRs when CI passes
- **[production-approval-rule.yaml](./examples/production-approval-rule.yaml)** - Require human approval for production
- **[dependabot-automerge-rule.yaml](./examples/dependabot-automerge-rule.yaml)** - Auto-merge Dependabot updates

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ 1. User creates PRReconciliationRule CRD (kubectl apply)    │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. AI Operator reads CRDs every N seconds                   │
│    └─> Fetches matching PRs via GitHub GraphQL API          │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. For each PR:                                              │
│    ├─> Render AI prompt with PR context                     │
│    ├─> Call AI (Claude/GPT) for decision                    │
│    └─> Parse JSON: {action: "merge|wait|escalate|close"}    │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. Execute action via GitHub GraphQL:                       │
│    ├─> merge: mergePullRequest mutation                     │
│    ├─> close: closePullRequest mutation + comment           │
│    ├─> escalate: add labels + comment                       │
│    └─> wait: do nothing, check next loop                    │
└─────────────────────────────────────────────────────────────┘
```

## ⚙️ Configuration

### Helm Values

Key configuration options in `values.yaml`:

```yaml
github:
  organization: "your-org"
  repositories:
    - "repo1"
    - "repo2"

ai:
  endpoint: "https://api.anthropic.com/v1/messages"
  model: "claude-3-5-sonnet-20241022"
  temperature: 0.2

argocd:
  enabled: true
  url: "https://argocd.example.com"

reconciliation:
  defaultIntervalSeconds: 30
  maxPRsPerCycle: 100
```

### Environment Variables

The operator supports these environment variables:

- `GITHUB_ORGANIZATION` - GitHub organization name
- `GITHUB_REPOSITORIES` - Comma-separated list of repos
- `GITHUB_TOKEN` - GitHub access token
- `AI_ENDPOINT` - AI API endpoint
- `AI_MODEL` - AI model name
- `AI_TOKEN` - AI API token
- `ARGOCD_ENABLED` - Enable Argo CD integration (true/false)
- `ARGOCD_URL` - Argo CD server URL
- `LOG_LEVEL` - Logging level (DEBUG, INFO, WARNING, ERROR)

## 🔒 Security

### RBAC

The operator requires these Kubernetes permissions:

- **PRReconciliationRule CRDs**: `get`, `list`, `watch`, `update` (status)
- **ConfigMaps**: `get`, `list` (for configuration)
- **Secrets**: `get`, `list` (for tokens)

### GitHub Permissions

Required GitHub token scopes:

- `repo` (full control of private repositories)
- `read:org` (read organization membership)
- `write:discussion` (for PR comments)

### Secrets Management

**Production best practice:** Use external secrets management (e.g., Sealed Secrets, External Secrets Operator):

```yaml
# values.yaml
github:
  tokenSecretName: github-token-sealed
  tokenSecretKey: token

ai:
  tokenSecretName: ai-token-sealed
  tokenSecretKey: token
```

## 🐛 Troubleshooting

### Operator not starting

Check logs:

```bash
kubectl -n ai-operator-system logs -f deployment/ai-operator
```

Common issues:
- Missing secrets (github-token, ai-token)
- CRD not installed
- RBAC permissions incorrect

### PRs not being processed

1. Check if rule matches PRs:

```bash
kubectl describe prrule <rule-name> -n ai-operator-system
```

2. Check operator logs for errors:

```bash
kubectl -n ai-operator-system logs deployment/ai-operator --tail=100
```

3. Verify PR labels match rule selector

### AI decisions not working

- Check AI API token is valid
- Verify AI endpoint is reachable
- Review AI prompt template syntax
- Check logs for AI errors

## 📊 Monitoring

The operator exposes Prometheus metrics on port 9090:

- `ai_operator_rules_total` - Total number of reconciliation rules
- `ai_operator_prs_processed_total` - Total PRs processed
- `ai_operator_actions_total{action="merge|close|escalate|wait"}` - Actions taken
- `ai_operator_errors_total{type="github|ai|kubernetes"}` - Errors by type

## 🤝 Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Submit a pull request

## 📄 License

MIT License - See [LICENSE](LICENSE) for details

## 🙏 Acknowledgments

Built with:
- [Kubernetes Python Client](https://github.com/kubernetes-client/python)
- [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python)
- [Jinja2](https://jinja.palletsprojects.com/)
- [Structlog](https://www.structlog.org/)

---

**Status:** ✅ Production Ready
**Version:** 0.1.0
**Last Updated:** 2026-02-27
