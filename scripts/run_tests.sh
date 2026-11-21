#!/usr/bin/env bash
# Runs all unit-test suites. No cloud, gateway, database server or LLM needed.
#   pip install -r services/tuition_api/requirements-dev.txt -r services/mcp_server/requirements-dev.txt -r agent/requirements-dev.txt
set -euo pipefail
cd "$(dirname "$0")/.."
exec python scripts/run_tests.py "$@"
