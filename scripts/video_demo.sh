#!/usr/bin/env bash
# Step-by-step demo for the screen recording, against the Azure deployment.
# Pauses before every section; writes nothing into docs/evidence/ (outputs go to traces/, git-ignored).
#   bash scripts/video_demo.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
GW="${GATEWAY_URL:-https://kong-gateway.proudmeadow-f5ce4b7c.swedencentral.azurecontainerapps.io}"
MCP="${MCP_URL:-https://mcp-server.proudmeadow-f5ce4b7c.swedencentral.azurecontainerapps.io/mcp}"
PY="$ROOT/.venv/Scripts/python.exe"; [[ -x "$PY" ]] || PY="$ROOT/.venv/bin/python"
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
mkdir -p traces/video

pause() { echo; read -r -p ">>> [Enter] $1 " _; echo; }
ask() {  # fresh agent session per question, so each scenario selects its own tools
  echo "================================================================"
  ( cd agent && GATEWAY_URL="$GW" MCP_URL="$MCP" STUDENT_PASSWORD='Passw0rd!' \
      "$PY" -u -m tuition_agent.cli --username s1001 --trace-dir ../traces/video -q "$1" )
}

echo "Warming up the cloud services (scale-to-zero cold start, up to ~45 s)..."
curl -s -o /dev/null -m 120 -w "  Swagger UI  -> HTTP %{http_code}\n" "$GW/api/docs"
curl -s -o /dev/null -m 120 -w "  MCP /health -> HTTP %{http_code}\n" "${MCP%/mcp}/health"
echo "  Swagger: $GW/api/docs"

pause "1/5 API Gateway: protected route 401/200, 403, per-student 429, payment, duplicate, CSV, paging"
GATEWAY_URL="$GW" EVIDENCE_LOG=traces/video/gateway-demo.log "$PY" scripts/gateway_demo.py

pause "2/5 MCP server: three retrieval queries over the MCP protocol"
"$PY" scripts/mcp_demo.py "$MCP"

pause "3/5 Agent - MCP only"
ask "When is the payment deadline for the Fall term?"
pause "3/5 Agent - REST API only"
ask "What is my balance?"
pause "3/5 Agent - MCP + REST API combined"
ask "Can I pay my outstanding balance in installments?"

pause "4/5 Agent - payment with explicit confirmation (just Enter here; type yes later at the confirmation prompt)"
ask "Pay 500 dollars of my 2026-FALL balance."
pause "4/5 Agent - authorization guard (another student's data)"
ask "What is the balance of student S1002?"

pause "5/5 Unit tests (no cloud, no live LLM)"
TEST_LOG=traces/video/unit-tests.log "$PY" scripts/run_tests.py 2>&1 | grep -E "^=+ (services|agent)|passed in|failed|Overall"
echo; echo "Demo finished."
