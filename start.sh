#!/usr/bin/env bash
# ==============================================================================
# Product Catalogue Integration — Service & Pipeline Runner
# ==============================================================================
# This script sets up dependencies, boots the external mock services (PIM & WMS),
# verifies their health, and runs the catalogue synchronisation pipeline.
#
# Usage:
#   ./start.sh              # Setup venv, start mocks, run synchronisation pipeline
#   ./start.sh --test       # Run automated test suite (pytest)
#   ./start.sh --resume ID  # Resume a previously interrupted run by ID
#   ./start.sh --mocks-only # Start mock services in background without running sync
#   ./start.sh --stop       # Stop running mock services (Docker)
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ANSI Colors
BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
RED="\033[0;31m"
CYAN="\033[0;36m"
NC="\033[0m"

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "\n${BOLD}${CYAN}==>${NC} ${BOLD}$1${NC}"
}

# ------------------------------------------------------------------------------
# 1. Handle --stop flag
# ------------------------------------------------------------------------------
if [[ "${1:-}" == "--stop" ]]; then
    log_step "Stopping mock services..."
    if command -v docker &>/dev/null && docker compose version &>/dev/null; then
        docker compose down
        log_info "Mock services stopped."
    else
        log_warn "Docker is not running or not found. No containers to stop."
    fi
    exit 0
fi

# ------------------------------------------------------------------------------
# 2. Python Virtual Environment & Dependencies
# ------------------------------------------------------------------------------
log_step "Checking Python environment..."

PYTHON_BIN=""
for cmd in python3.11 python3.12 python3; do
    if command -v "$cmd" &>/dev/null; then
        PYTHON_BIN="$cmd"
        break
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    log_error "No Python 3 interpreter found. Please install Python 3.9+ (Python 3.11+ recommended)."
    exit 1
fi

log_info "Using Python interpreter: $($PYTHON_BIN --version) ($PYTHON_BIN)"

if [[ ! -d ".venv" ]]; then
    log_info "Creating virtual environment in .venv..."
    "$PYTHON_BIN" -m venv .venv
fi

VENV_PYTHON=".venv/bin/python"
VENV_PIP=".venv/bin/pip"
VENV_PYTEST=".venv/bin/pytest"

# Upgrade pip and install requirements if not installed
if ! "$VENV_PYTHON" -c "import httpx, tenacity, structlog, fastapi" &>/dev/null; then
    log_info "Installing dependencies from requirements.txt..."
    "$VENV_PIP" install -q -r requirements.txt
else
    log_info "Dependencies are already installed."
fi

# ------------------------------------------------------------------------------
# 3. Handle --test flag
# ------------------------------------------------------------------------------
if [[ "${1:-}" == "--test" ]]; then
    log_step "Running test suite (pytest)..."
    "$VENV_PYTEST" -v
    exit 0
fi

# ------------------------------------------------------------------------------
# 4. Start Mock Services (Docker Compose)
# ------------------------------------------------------------------------------
log_step "Checking mock external services..."

start_docker_mocks() {
    if ! command -v docker &>/dev/null; then
        log_error "Docker is not installed or not in PATH."
        log_error "Docker Desktop is required to run the mock PIM and WMS APIs."
        return 1
    fi

    if ! docker info &>/dev/null; then
        log_error "Docker daemon is not running. Please start Docker Desktop."
        return 1
    fi

    log_info "Starting mock services via Docker Compose..."
    docker compose up -d --build
    return 0
}

wait_for_service() {
    local url="$1"
    local name="$2"
    local max_retries=30
    local count=0

    log_info "Waiting for $name to be healthy at $url..."
    while [[ $count -lt $max_retries ]]; do
        if curl -s -f "$url" &>/dev/null; then
            log_info "$name is ready!"
            return 0
        fi
        sleep 1
        count=$((count + 1))
        echo -n "."
    done
    echo ""
    log_error "Timed out waiting for $name to become healthy."
    return 1
}

# Check if mock services are already running on ports 8001 and 8002
PIM_HEALTH="http://localhost:8001/health"
WMS_HEALTH="http://localhost:8002/health"

if curl -s -f "$PIM_HEALTH" &>/dev/null && curl -s -f "$WMS_HEALTH" &>/dev/null; then
    log_info "Mock services are already running and healthy."
else
    if start_docker_mocks; then
        wait_for_service "$PIM_HEALTH" "Product API (PIM)"
        wait_for_service "$WMS_HEALTH" "Warehouse API (WMS)"
    else
        log_warn "Could not start mock services via Docker."
        log_warn "If running locally without Docker, ensure services are accessible at:"
        log_warn "  - Product API:   http://localhost:8001"
        log_warn "  - Warehouse API: http://localhost:8002"
        log_warn "You can still run automated tests without Docker using: ./start.sh --test"
        if [[ "${1:-}" != "--mocks-only" ]]; then
            exit 1
        fi
    fi
fi

if [[ "${1:-}" == "--mocks-only" ]]; then
    log_info "Mock services are running."
    log_info "Product API:   http://localhost:8001 (Swagger docs: http://localhost:8001/docs)"
    log_info "Warehouse API: http://localhost:8002 (Swagger docs: http://localhost:8002/docs)"
    exit 0
fi

# ------------------------------------------------------------------------------
# 5. Run the Catalogue Integration Pipeline
# ------------------------------------------------------------------------------
log_step "Running PIM to WMS integration pipeline..."

if [[ "${1:-}" == "--resume" ]]; then
    if [[ -z "${2:-}" ]]; then
        log_error "Missing run ID for --resume. Usage: ./start.sh --resume <RUN_ID>"
        exit 1
    fi
    log_info "Resuming sync run: $2"
    "$VENV_PYTHON" -m app.main --resume "$2"
else
    log_info "Starting fresh synchronisation run..."
    "$VENV_PYTHON" -m app.main
fi

log_step "Pipeline execution complete!"
