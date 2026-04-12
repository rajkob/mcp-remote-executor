#!/usr/bin/env bash
# Remote Executor MCP Server — Bash Deploy Script
# Works on Linux and macOS. Does NOT require Python.
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh                # full deploy (with dashboard)
#   ./deploy.sh --no-dashboard # deploy MCP only, no dashboard UI
#   ./deploy.sh --pull         # pull from Docker Hub instead of building
#   ./deploy.sh --restart      # restart running container
#   ./deploy.sh --status       # check if server is running

set -euo pipefail

DOCKER_IMAGE="rajkob/mcp-remote-executor:latest"
MCP_PORT=8765
MCP_URL="http://127.0.0.1:${MCP_PORT}/sse"
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARD=true
USE_PULL=false
RESTART=false
STATUS=false

# ── Colours ──────────────────────────────────────────────────────────────────
GREEN="\033[92m"; YELLOW="\033[93m"; RED="\033[91m"
CYAN="\033[96m";  BOLD="\033[1m";    RESET="\033[0m"

ok()   { echo -e "${GREEN}✓${RESET} $*"; }
warn() { echo -e "${YELLOW}⚠${RESET}  $*"; }
err()  { echo -e "${RED}✗${RESET} $*"; }
info() { echo -e "${CYAN}→${RESET} $*"; }
head() { echo -e "\n${BOLD}$*${RESET}"; }

# ── Argument parsing ──────────────────────────────────────────────────────────
for arg in "$@"; do
  case $arg in
    --no-dashboard) DASHBOARD=false ;;
    --pull)         USE_PULL=true ;;
    --restart)      RESTART=true ;;
    --status)       STATUS=true ;;
    -h|--help)
      echo "Usage: $0 [--no-dashboard] [--pull] [--restart] [--status]"
      exit 0
      ;;
    *) err "Unknown argument: $arg"; exit 1 ;;
  esac
done

echo -e "\n${BOLD}Remote Executor MCP Server — Deployment${RESET}"
echo "Platform : $(uname -s) $(uname -r)"
echo "Project  : ${BASE_DIR}"

# ── Status check ──────────────────────────────────────────────────────────────
if $STATUS; then
  head "Server Status"
  if curl -sf --max-time 3 "${MCP_URL}" > /dev/null 2>&1; then
    ok "MCP server is RUNNING on port ${MCP_PORT}"
  else
    err "MCP server NOT reachable on port ${MCP_PORT}"
    docker compose ps 2>/dev/null || true
  fi
  exit 0
fi

# ── Restart only ──────────────────────────────────────────────────────────────
if $RESTART; then
  head "Restarting container"
  cd "${BASE_DIR}"
  docker compose restart
  ok "Container restarted."
  exit 0
fi

# ── Step 1: Prerequisites ─────────────────────────────────────────────────────
head "[ 1 / 5 ]  Prerequisites"

if docker info > /dev/null 2>&1; then
  ok "Docker is running."
else
  err "Docker daemon not running. Start Docker and retry."
  exit 1
fi

if docker compose version > /dev/null 2>&1; then
  ok "Docker Compose: $(docker compose version --short 2>/dev/null || echo 'ok')"
else
  err "Docker Compose plugin not found."
  exit 1
fi

# ── Step 2: Data directory + .env ─────────────────────────────────────────────
head "[ 2 / 5 ]  Initialisation"

cd "${BASE_DIR}"
DATA_DIR="${BASE_DIR}/data"
ENV_FILE="${BASE_DIR}/.env"

mkdir -p "${DATA_DIR}/output"
ok "data/ directory ready."

# vms.yaml skeleton
VMS_FILE="${DATA_DIR}/vms.yaml"
if [[ ! -f "${VMS_FILE}" || ! -s "${VMS_FILE}" ]]; then
  cat > "${VMS_FILE}" << 'YAML'
defaults:
  user: root
  port: 22
  timeout: 30

templates:
  disk:            "df -h"
  memory:          "free -h"
  uptime:          "uptime"
  cpu:             "top -bn1 | grep 'Cpu(s)'"
  who:             "who"
  os-version:      "cat /etc/os-release"
  failed-services: "systemctl --failed"
  netstat:         "ss -tlnp"

projects: {}
YAML
  ok "vms.yaml skeleton created."
else
  ok "vms.yaml already exists."
fi

# Generate CRED_MASTER_KEY using Docker (no Python needed on host)
if [[ -f "${ENV_FILE}" ]] && grep -q "^CRED_MASTER_KEY=" "${ENV_FILE}" 2>/dev/null; then
  ok "CRED_MASTER_KEY already in .env"
else
  info "Generating CRED_MASTER_KEY via Docker..."
  CRED_KEY=$(docker run --rm "${DOCKER_IMAGE}" python -c \
    "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null \
    || docker run --rm "mcp-remote-executor:latest" python -c \
    "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
  echo "CRED_MASTER_KEY=${CRED_KEY}" >> "${ENV_FILE}"
  ok "CRED_MASTER_KEY written to .env"
fi

# Empty credentials file
CRED_FILE="${DATA_DIR}/credentials"
if [[ ! -f "${CRED_FILE}" ]]; then
  echo -n "{}" > "${CRED_FILE}"
  ok "credentials file initialised."
fi

# ── Step 3: API Key ───────────────────────────────────────────────────────────
head "[ 3 / 5 ]  API Key Authentication"

ENV_CONTENT="$(cat "${ENV_FILE}" 2>/dev/null || echo '')"
API_KEY=""

if echo "${ENV_CONTENT}" | grep -q "^MCP_API_KEY=.\+"; then
  API_KEY=$(grep "^MCP_API_KEY=" "${ENV_FILE}" | cut -d= -f2-)
  ok "API key already configured (length: ${#API_KEY})."
else
  echo "  Enable API key authentication?"
  echo "  Recommended for shared/remote deployments."
  echo -n "  [Y] Generate key  [N] Skip (Y/n): "
  read -r CHOICE
  CHOICE="${CHOICE:-y}"
  if [[ "${CHOICE,,}" == "n" || "${CHOICE,,}" == "no" ]]; then
    warn "Auth DISABLED — ensure port ${MCP_PORT} is firewalled."
  else
    # Generate 32-byte URL-safe base64 token
    API_KEY=$(openssl rand -base64 32 | tr -d '\n')
    echo "MCP_API_KEY=${API_KEY}" >> "${ENV_FILE}"
    # Uncomment in docker-compose.yml
    sed -i 's|# - MCP_API_KEY=\${MCP_API_KEY}|- MCP_API_KEY=${MCP_API_KEY}|' docker-compose.yml 2>/dev/null || true
    ok "API key written to .env"
    echo -e "\n  ${BOLD}Your API key:${RESET} ${CYAN}${API_KEY}${RESET}"
    echo -e "  ${YELLOW}Copy this — you'll need it in your client config.${RESET}"
  fi
fi

# ── Dashboard toggle in compose ───────────────────────────────────────────────
if $DASHBOARD; then
  sed -i 's|MCP_DASHBOARD=false|MCP_DASHBOARD=true|' docker-compose.yml 2>/dev/null || true
  ok "Dashboard: ENABLED  (http://localhost:${MCP_PORT}/dashboard)"
else
  sed -i 's|MCP_DASHBOARD=true|MCP_DASHBOARD=false|' docker-compose.yml 2>/dev/null || true
  warn "Dashboard: DISABLED  (use --dashboard to enable)"
fi

# ── Step 4: Build / pull + start ──────────────────────────────────────────────
head "[ 4 / 5 ]  Docker Deploy"

if $USE_PULL; then
  info "Pulling ${DOCKER_IMAGE} ..."
  docker pull "${DOCKER_IMAGE}"
  ok "Image pulled."
else
  info "Building image from source..."
  docker compose build
  ok "Image built."
fi

info "Starting container (docker compose up -d)..."
docker compose up -d
ok "Container started."

# ── Step 5: Health check ──────────────────────────────────────────────────────
head "[ 5 / 5 ]  Health Check"
info "Waiting for MCP server at ${MCP_URL} (up to 30s)..."

ATTEMPT=0
DEADLINE=$((SECONDS + 30))
until curl -sf --max-time 3 "${MCP_URL}" > /dev/null 2>&1; do
  ATTEMPT=$((ATTEMPT + 1))
  if [[ $SECONDS -ge $DEADLINE ]]; then
    err "Server did not respond within 30s."
    err "Check logs: docker compose logs remote-executor"
    exit 1
  fi
  printf "  Attempt %d — not ready yet...\r" "$ATTEMPT"
  sleep 2
done
ok "Server is UP — ${MCP_URL}"

# ── Integration info ──────────────────────────────────────────────────────────
SEP="============================================================"
echo ""
echo "${SEP}"
echo -e "${BOLD}  MCP Server is ready — Integration Guide${RESET}"
echo "${SEP}"

AUTH_HEADER=""
if [[ -n "${API_KEY}" ]]; then
  echo -e "\n${BOLD}API Key:${RESET}  ${CYAN}${API_KEY}${RESET}  ${YELLOW}← keep this safe${RESET}"
  AUTH_HEADER='"headers": { "X-MCP-Key": "'"${API_KEY}"'" }'
fi

echo -e "
${BOLD}Server URL:${RESET}  ${CYAN}http://localhost:${MCP_PORT}/sse${RESET}
${BOLD}Dashboard:${RESET}   ${CYAN}http://localhost:${MCP_PORT}/dashboard${RESET}

${BOLD}── VS Code (GitHub Copilot Agent) ──${RESET}
Add to User Settings JSON:

  ${CYAN}\"mcp\": {
    \"servers\": {
      \"remote-executor\": {
        \"type\": \"sse\",
        \"url\": \"http://localhost:${MCP_PORT}/sse\"$([ -n "${AUTH_HEADER}" ] && echo ",
        ${AUTH_HEADER}")
      }
    }
  }${RESET}

${BOLD}── Claude Desktop ──${RESET}
Add to ~/.config/Claude/claude_desktop_config.json:

  ${CYAN}\"mcpServers\": {
    \"remote-executor\": {
      \"command\": \"npx\",
      \"args\": [\"-y\", \"mcp-remote\", \"http://localhost:${MCP_PORT}/sse\"]
    }
  }${RESET}

${BOLD}── Useful Docker commands ──${RESET}
  docker compose logs -f remote-executor   # live logs
  docker compose restart remote-executor   # restart
  docker compose down                      # stop
  docker compose up -d                     # start
"
echo "${SEP}"
