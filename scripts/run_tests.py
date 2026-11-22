"""Run every unit suite on Windows/Linux and save a UTF-8 execution log.

Usage: python scripts/run_tests.py [pytest options]
No running services or LLM credentials are needed.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
SUITES = ("services/tuition_api", "services/mcp_server", "agent")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    log_path = ROOT / os.getenv("TEST_LOG", "docs/evidence/unit-tests.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    temp_root = ROOT / ".test-tmp"
    temp_root.mkdir(exist_ok=True)
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    status = 0
    with log_path.open("w", encoding="utf-8") as log:
        def emit(text: str) -> None:
            print(text, end="", flush=True)
            log.write(text)
            log.flush()

        emit(f"Run: {datetime.now(timezone.utc).isoformat()}\nPython: {sys.version.split()[0]}\n")
        for suite in SUITES:
            emit(f"\n================ {suite} ================\n")
            # Use a new workspace-local temp folder instead of the Windows shared pytest folder.
            with tempfile.TemporaryDirectory(prefix="pytest-", dir=temp_root) as temp:
                result = subprocess.run(
                    [sys.executable, "-m", "pytest", "-v", f"--basetemp={Path(temp) / 'data'}", *sys.argv[1:]],
                    cwd=ROOT / suite, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace",
                )
                emit(result.stdout)
                if result.returncode:
                    status = 1
        emit(f"\nOverall result: {'FAILED' if status else 'PASSED'}\n")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
