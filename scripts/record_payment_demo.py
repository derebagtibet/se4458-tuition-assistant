"""Record the student-confirmed agent payment demo to docs/evidence/agent-payment-confirmed.log.

Runs the real CLI (real Gemini, MCP and Kong gateway) for one payment question and mirrors
everything printed on the console - including the confirmation prompt and the student's typed
answer - into a UTF-8 log. The student must type the confirmation; nothing is auto-confirmed.

    python scripts/record_payment_demo.py            # from the project root, stack running
"""
from __future__ import annotations

import builtins
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "docs" / "evidence" / "agent-payment-confirmed.log"
QUESTION = "Pay 500 dollars of my 2026-FALL balance."


class Tee:
    def __init__(self, console, log):
        self.console, self.log = console, log
        self.encoding = "utf-8"

    def write(self, s):
        self.console.write(s)
        self.log.write(s)
        return len(s)

    def flush(self):
        self.console.flush()
        self.log.flush()

    def isatty(self):  # keep the log free of ANSI colour codes
        return False


def main() -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("STUDENT_PASSWORD", "Passw0rd!")  # seeded demo account (app/seed.py)
    with LOG.open("w", encoding="utf-8") as log:
        sys.stdout = Tee(sys.__stdout__, log)
        sys.stderr = Tee(sys.__stderr__, log)
        real_input = builtins.input

        def logged_input(prompt=""):
            answer = real_input(prompt)
            log.write(f"{answer}   <- typed by the student\n")
            log.flush()
            return answer

        builtins.input = logged_input
        print(f"# Recorded {datetime.now().astimezone().isoformat(timespec='seconds')}")
        print(f"# Command: python -m tuition_agent.cli --username s1001 -q \"{QUESTION}\" "
              f"--trace-dir docs/evidence/agent\n")
        sys.path.insert(0, str(ROOT / "agent"))
        from tuition_agent import cli  # imported after the Tee so colours stay off

        sys.argv = ["tuition_agent.cli", "--username", "s1001", "-q", QUESTION,
                    "--trace-dir", str(ROOT / "docs" / "evidence" / "agent")]
        try:
            cli.main()
        finally:
            sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
    print(f"\nLog saved: {LOG}")


if __name__ == "__main__":
    main()
