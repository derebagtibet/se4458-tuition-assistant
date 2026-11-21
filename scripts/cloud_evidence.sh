#!/usr/bin/env bash
# Collects cloud evidence against the Azure deployment (run from anywhere, in Git Bash):
#   bash scripts/cloud_evidence.sh            # all three steps
#   bash scripts/cloud_evidence.sh gateway    # or: mcp | agent | combined
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
GW="${GATEWAY_URL:-https://kong-gateway.proudmeadow-f5ce4b7c.swedencentral.azurecontainerapps.io}"
MCP="${MCP_URL:-https://mcp-server.proudmeadow-f5ce4b7c.swedencentral.azurecontainerapps.io/mcp}"
PY="$ROOT/.venv/Scripts/python.exe"
[[ -x "$PY" ]] || PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || { echo "Python venv not found at .venv"; exit 1; }
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
STEP="${1:-all}"

if [[ "$STEP" == all || "$STEP" == gateway ]]; then
  echo "=== 1/3 Gateway demo against $GW (first call may take ~45 s: cold start)"
  GATEWAY_URL="$GW" EVIDENCE_LOG=docs/evidence/cloud-gateway-demo.log "$PY" scripts/gateway_demo.py
fi

if [[ "$STEP" == all || "$STEP" == mcp ]]; then
  echo "=== 2/3 MCP retrieval demo against $MCP"
  "$PY" scripts/mcp_demo.py "$MCP" 2>&1 | tee docs/evidence/cloud-mcp-retrieval-demo.log
fi

if [[ "$STEP" == all || "$STEP" == agent ]]; then
  echo "=== 3/3 Agent demo (5 scenarios; type 'yes' at the payment confirmation)"
  cd agent
  GATEWAY_URL="$GW" MCP_URL="$MCP" STUDENT_PASSWORD='Passw0rd!' \
    "$PY" -u -m tuition_agent.cli --username s1001 --trace-dir ../docs/evidence/agent-cloud --demo 2>&1 \
    | tee ../docs/evidence/cloud-agent-demo.log
fi
if [[ "$STEP" == combined ]]; then
  echo "=== Combined MCP + REST scenario in a fresh agent session (no earlier answers to reuse)"
  cd agent
  GATEWAY_URL="$GW" MCP_URL="$MCP" STUDENT_PASSWORD='Passw0rd!' \
    "$PY" -u -m tuition_agent.cli --username s1001 --trace-dir ../docs/evidence/agent-cloud \
    -q "Can I pay my outstanding balance in installments?" 2>&1 \
    | tee ../docs/evidence/cloud-agent-combined.log
  cd ..
fi
echo "=== done. Logs are in docs/evidence/cloud-*.log"
