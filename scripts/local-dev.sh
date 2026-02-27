#!/usr/bin/env bash
#
# Local development helper for AI-Driven PR Reconciliation Operator
#
# This script helps you:
# - Set up a local Kubernetes cluster (kind)
# - Build and load the Docker image
# - Install the Helm chart
# - Create example secrets
# - Apply sample PR rules
#

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info() {
    echo -e "${GREEN}==>${NC} $*"
}

warn() {
    echo -e "${YELLOW}WARNING:${NC} $*"
}

error() {
    echo -e "${RED}ERROR:${NC} $*"
    exit 1
}

# Check prerequisites
check_prerequisites() {
    info "Checking prerequisites..."

    local missing=()

    command -v docker >/dev/null 2>&1 || missing+=("docker")
    command -v kind >/dev/null 2>&1 || missing+=("kind")
    command -v kubectl >/dev/null 2>&1 || missing+=("kubectl")
    command -v helm >/dev/null 2>&1 || missing+=("helm")

    if [ ${#missing[@]} -gt 0 ]; then
        error "Missing required tools: ${missing[*]}"
    fi

    info "All prerequisites satisfied"
}

# Create kind cluster
create_cluster() {
    local cluster_name="${1:-ai-operator-dev}"

    if kind get clusters 2>/dev/null | grep -q "^${cluster_name}$"; then
        warn "Cluster '${cluster_name}' already exists. Skipping creation."
        return 0
    fi

    info "Creating kind cluster: ${cluster_name}"

    cat <<EOF | kind create cluster --name="${cluster_name}" --config=-
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
  extraPortMappings:
  - containerPort: 30080
    hostPort: 8080
    protocol: TCP
  - containerPort: 30090
    hostPort: 9090
    protocol: TCP
EOF

    info "Cluster created successfully"
}

# Build and load Docker image
build_and_load_image() {
    local cluster_name="${1:-ai-operator-dev}"
    local image_tag="ai-devops-operator:dev"

    info "Building Docker image: ${image_tag}"
    docker build -t "${image_tag}" .

    info "Loading image into kind cluster"
    kind load docker-image "${image_tag}" --name="${cluster_name}"

    info "Image loaded successfully"
}

# Create namespace and secrets
setup_namespace() {
    local namespace="${1:-ai-operator-system}"

    info "Creating namespace: ${namespace}"
    kubectl create namespace "${namespace}" --dry-run=client -o yaml | kubectl apply -f -

    # Prompt for tokens if not set
    if [ -z "${GITHUB_TOKEN:-}" ]; then
        warn "GITHUB_TOKEN not set in environment"
        echo -n "Enter GitHub token (or press Enter to use dummy value for testing): "
        read -r github_token
        GITHUB_TOKEN="${github_token:-ghp_dummy_token_for_testing}"
    fi

    if [ -z "${AI_TOKEN:-}" ]; then
        warn "AI_TOKEN not set in environment"
        echo -n "Enter AI token (or press Enter to use dummy value for testing): "
        read -r ai_token
        AI_TOKEN="${ai_token:-sk-ant-dummy_token_for_testing}"
    fi

    info "Creating secrets in namespace: ${namespace}"
    kubectl create secret generic ai-operator-secrets \
        --namespace="${namespace}" \
        --from-literal=github-token="${GITHUB_TOKEN}" \
        --from-literal=ai-token="${AI_TOKEN}" \
        --dry-run=client -o yaml | kubectl apply -f -

    info "Secrets created successfully"
}

# Install Helm chart
install_helm_chart() {
    local namespace="${1:-ai-operator-system}"
    local org="${2:-example-org}"
    local repos="${3:-example-repo}"

    info "Installing Helm chart"

    helm upgrade --install ai-operator ./charts/ai-operator \
        --namespace="${namespace}" \
        --set image.repository=ai-devops-operator \
        --set image.tag=dev \
        --set image.pullPolicy=Never \
        --set github.organization="${org}" \
        --set github.repositories="{${repos}}" \
        --set replicaCount=1 \
        --wait

    info "Helm chart installed successfully"
}

# Apply example rules
apply_examples() {
    local namespace="${1:-ai-operator-system}"

    if [ ! -d "examples" ]; then
        warn "No examples directory found. Skipping."
        return 0
    fi

    info "Applying example PR rules"

    for example in examples/*.yaml; do
        if [ -f "${example}" ]; then
            info "Applying: $(basename "${example}")"
            kubectl apply -f "${example}" -n "${namespace}"
        fi
    done

    info "Example rules applied successfully"
}

# Show status
show_status() {
    local namespace="${1:-ai-operator-system}"

    info "Deployment status:"
    echo ""

    kubectl -n "${namespace}" get pods
    echo ""

    kubectl -n "${namespace}" get prrules
    echo ""

    info "Useful commands:"
    echo "  # View logs"
    echo "  kubectl -n ${namespace} logs -f deployment/ai-operator"
    echo ""
    echo "  # Port-forward metrics"
    echo "  kubectl -n ${namespace} port-forward deployment/ai-operator 9090:9090"
    echo ""
    echo "  # Port-forward health endpoint"
    echo "  kubectl -n ${namespace} port-forward deployment/ai-operator 8080:8080"
    echo ""
    echo "  # Edit a rule"
    echo "  kubectl -n ${namespace} edit prrule staging-automerge-rule"
    echo ""
    echo "  # Delete the cluster"
    echo "  kind delete cluster --name=ai-operator-dev"
}

# Main setup flow
main() {
    local cluster_name="ai-operator-dev"
    local namespace="ai-operator-system"
    local org="your-org"
    local repos="your-repo"

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --cluster)
                cluster_name="$2"
                shift 2
                ;;
            --namespace)
                namespace="$2"
                shift 2
                ;;
            --org)
                org="$2"
                shift 2
                ;;
            --repos)
                repos="$2"
                shift 2
                ;;
            --skip-cluster)
                SKIP_CLUSTER=1
                shift
                ;;
            --skip-build)
                SKIP_BUILD=1
                shift
                ;;
            --help)
                cat <<EOF
Usage: $0 [OPTIONS]

Options:
  --cluster NAME      Kind cluster name (default: ai-operator-dev)
  --namespace NS      Kubernetes namespace (default: ai-operator-system)
  --org ORG          GitHub organization
  --repos REPOS      Comma-separated repository list
  --skip-cluster     Skip cluster creation (use existing)
  --skip-build       Skip Docker build (use existing image)
  --help             Show this help message

Environment variables:
  GITHUB_TOKEN       GitHub personal access token
  AI_TOKEN          Anthropic API token

Example:
  export GITHUB_TOKEN=ghp_...
  export AI_TOKEN=sk-ant-...
  $0 --org=myorg --repos=repo1,repo2
EOF
                exit 0
                ;;
            *)
                error "Unknown option: $1 (use --help for usage)"
                ;;
        esac
    done

    info "Starting local development setup"
    echo ""

    check_prerequisites

    if [ -z "${SKIP_CLUSTER:-}" ]; then
        create_cluster "${cluster_name}"
    fi

    if [ -z "${SKIP_BUILD:-}" ]; then
        build_and_load_image "${cluster_name}"
    fi

    setup_namespace "${namespace}"
    install_helm_chart "${namespace}" "${org}" "${repos}"
    apply_examples "${namespace}"

    echo ""
    info "Setup complete!"
    echo ""

    show_status "${namespace}"
}

# Run main
main "$@"
