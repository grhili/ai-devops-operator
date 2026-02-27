# Contributing

## Prerequisites

- Python 3.11+
- A Kubernetes cluster or [kind](https://kind.sigs.k8s.io/) for local testing
- GitHub token with `repo` scope
- Anthropic API token

## Setup

```bash
git clone https://github.com/grhili/ai-devops-operator.git
cd ai-devops-operator
pip install -r requirements.txt
```

## Running Tests

```bash
pytest tests/ -v
```

No cluster or API tokens required — all external calls are mocked.

## Project Layout

```
src/
  main.py          # Entry point
  reconciler.py    # Orchestration loop
  github/          # GitHub GraphQL client (gql)
  k8s/             # Kubernetes CRD client (kubernetes)
  argocd/          # Argo CD REST client (aiohttp)
tests/
  conftest.py      # Shared fixtures
  test_github_client.py
  test_kubernetes_client.py
  test_reconciler.py
charts/ai-operator/  # Helm chart
examples/            # Sample PRReconciliationRule CRDs
```

## Making Changes

1. **Fork** the repo and create a branch from `main`
2. **Write tests** for any new behaviour — all tests live in `tests/`
3. **Run the test suite** and make sure it passes
4. **Open a PR** — the template will guide you through the description

## Adding a New Action

PR actions (`merge`, `close`, `escalate`, `wait`) are handled in
`src/reconciler.py` inside `_process_pr`. To add a new one:

1. Add the branch in `_process_pr`
2. Add the corresponding GraphQL mutation in `src/github/client.py` if needed
3. Cover it with a test in `tests/test_reconciler.py`

## Adding a New Rule Field

CRD schema lives in `charts/ai-operator/templates/crd.yaml`. To add a field:

1. Add it to the `openAPIV3Schema` in the CRD
2. Add a property to `PRReconciliationRule` in `src/k8s/client.py`
3. Use it in `src/reconciler.py`
4. Update the examples in `examples/`

## Coding Style

- Keep functions small and focused
- No inline `os.getenv` outside `Reconciler.__init__` — configuration is read once at startup
- All GitHub operations go through `src/github/client.py` — no direct HTTP calls from `reconciler.py`
- AI prompt templates belong in the CRD `instruction` field, not hardcoded in Python

## Commit Messages

Use the format `type: short description`, e.g.:

```
feat: add support for draft PR filtering
fix: handle missing statusCheckRollup gracefully
chore: upgrade gql to 3.6
docs: add kopf migration notes
```

## Reporting Issues

Open an issue at https://github.com/grhili/ai-devops-operator/issues with:
- What you expected to happen
- What actually happened
- Relevant log output (`kubectl logs deployment/ai-operator -n ai-operator-system`)
