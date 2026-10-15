#!/usr/bin/env bash
# ==============================================================================
# Product Catalogue Integration — Service & Pipeline Runner
# ==============================================================================
# This script sets up dependencies, boots the external mock services (PIM & WMS),
# verifies their health, cleans up previous migration/run state on fresh starts,
# and runs the catalogue synchronisation pipeline.
#
# Supports both:
#   - Docker Compose mode (if Docker Desktop / daemon is running)
#   - Direct Python mode (via run_mock_services.py fallback if Docker is inactive)
#
# Usage:
#   ./start.sh              # Start mocks and run pipeline (preserves run history)
#   ./start.sh --list       # List all sync runs and their statuses
#   ./start.sh --clean      # Clean up previous migration data and state only
#   ./start.sh --clean-run  # Clean previous state and start a fresh run
#   ./start.sh --test       # Run automated test suite (pytest)
#   ./start.sh --resume ID  # Resume an interrupted run by ID (preserves state)
#   ./start.sh --mocks-only # Start mock services only
#   ./start.sh --stop       # Stop running mock services
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PID_FILE=".mock_services.pid"

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

clean_previous_migration() {
    log_step "Cleaning up previous migration data, database state, and exports..."
    if [[ -d "runtime/exports" ]]; then
        rm -rf runtime/exports/* 2>/dev/null || true
    fi
    rm -rf runtime/*.db* runtime/state.db* 2>/dev/null || true
    mkdir -p runtime/exports
    log_info "State database (state.db) and export files cleaned up successfully."
}

# ------------------------------------------------------------------------------
# 1. Handle --clean flag
# ------------------------------------------------------------------------------
if [[ "${1:-}" == "--clean" ]]; then
    clean_previous_migration
    exit 0
fi

# ------------------------------------------------------------------------------
# 2. Handle --stop flag
# ------------------------------------------------------------------------------
if [[ "${1:-}" == "--stop" ]]; then
    log_step "Stopping mock services..."

    # Stop native Python runner if running
    if [[ -f "$PID_FILE" ]]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null || true
            pkill -f "run_mock_services.py" 2>/dev/null || true
            log_info "Stopped background Python mock services (PID $PID)."
        fi
        rm -f "$PID_FILE"
    fi

    # Stop Docker Compose if running
    if command -v docker &>/dev/null && docker compose version &>/dev/null; then
        docker compose down 2>/dev/null || true
        log_info "Docker mock services stopped."
    fi

    log_info "All mock services stopped."
    exit 0
fi

# ------------------------------------------------------------------------------
# 3. Python Virtual Environment & Dependencies
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

# Install requirements if not installed
if ! "$VENV_PYTHON" -c "import httpx, tenacity, structlog, fastapi, eval_type_backport" &>/dev/null; then
    log_info "Installing dependencies from requirements.txt..."
    "$VENV_PIP" install -q -r requirements.txt eval-type-backport
else
    log_info "Dependencies are already installed."
fi

# ------------------------------------------------------------------------------
# 4. Handle --test flag
# ------------------------------------------------------------------------------
if [[ "${1:-}" == "--test" ]]; then
    log_step "Running test suite (pytest)..."
    "$VENV_PYTEST" -v
    exit 0
fi

# ------------------------------------------------------------------------------
# 5. Handle --list / --status flag
# ------------------------------------------------------------------------------
if [[ "${1:-}" == "--list" || "${1:-}" == "--status" ]]; then
    log_step "Fetching sync runs from state store..."
    "$VENV_PYTHON" -m app.main --list-runs
    exit 0
fi

# ------------------------------------------------------------------------------
# 6. Clean Previous Migration Data (only when --clean-run requested)
# ------------------------------------------------------------------------------
if [[ "${1:-}" == "--clean-run" ]]; then
    clean_previous_migration
else
    log_info "Preserving previous migration and state data in state store."
fi

# ------------------------------------------------------------------------------
# 6. Start Mock Services (Docker or Native Python fallback)
# ------------------------------------------------------------------------------
log_step "Checking mock external services..."

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

PIM_HEALTH="http://localhost:8001/health"
WMS_HEALTH="http://localhost:8002/health"

if curl -s -f "$PIM_HEALTH" &>/dev/null && curl -s -f "$WMS_HEALTH" &>/dev/null; then
    log_info "Mock services are already running and healthy."
else
    DOCKER_AVAILABLE=false
    if command -v docker &>/dev/null; then
        if docker info &>/dev/null; then
            DOCKER_AVAILABLE=true
        fi
    fi

    if [[ "$DOCKER_AVAILABLE" == "true" ]]; then
        log_info "Docker daemon detected. Starting mock services via Docker Compose..."
        docker compose up -d --build
    else
        log_info "Docker daemon not running. Launching mock services natively via run_mock_services.py..."
        nohup "$VENV_PYTHON" run_mock_services.py > runtime/mock_services.log 2>&1 &
        echo $! > "$PID_FILE"
    fi

    wait_for_service "$PIM_HEALTH" "Product API (PIM)"
    wait_for_service "$WMS_HEALTH" "Warehouse API (WMS)"
fi

if [[ "${1:-}" == "--mocks-only" ]]; then
    log_info "Mock services are running."
    log_info "Product API:   http://localhost:8001 (Swagger docs: http://localhost:8001/docs)"
    log_info "Warehouse API: http://localhost:8002 (Swagger docs: http://localhost:8002/docs)"
    exit 0
fi

# ------------------------------------------------------------------------------
# 7. Run the Catalogue Integration Pipeline
# ------------------------------------------------------------------------------
log_step "Running PIM to WMS integration pipeline..."

if [[ "${1:-}" == "--resume" ]]; then
    if [[ -z "${2:-}" ]]; then
        log_error "Missing run ID for --resume. Usage: ./start.sh --resume <RUN_ID>"
        exit 1
    fi
    log_info "Resuming sync run: $2"
    "$VENV_PYTHON" -m app.main --resume "$2"
elif [[ "${1:-}" == "--clean-run" ]]; then
    log_info "Starting clean synchronisation run..."
    "$VENV_PYTHON" -m app.main --clean
else
    log_info "Starting synchronisation run (preserving history)..."
    "$VENV_PYTHON" -m app.main
fi

log_step "Pipeline execution complete!"
