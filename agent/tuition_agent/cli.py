"""Command-line chat for the AI Tuition Assistant.

    python -m tuition_agent.cli                      # interactive chat (asks for username/password)
    python -m tuition_agent.cli --demo               # runs the assignment's demo questions in order
    python -m tuition_agent.cli -q "What is my balance?"
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
import httpx

from .agent import AgentResult, TuitionAgent
from .backends import GatewayClient, GatewayError, PolicyMcpClient
from .llm import GeminiLLM, LLMError

DEMO_QUESTIONS = [
    ("MCP only", "When is the payment deadline for the Fall term?"),
    ("REST API only", "What is my balance?"),
    ("MCP + REST API", "Can I pay my outstanding balance in installments?"),
    ("Payment (confirmation)", "Pay 500 dollars of my 2026-FALL balance."),
    ("Authorization guard", "What is the balance of student S1002?"),
]

C = {"b": "\033[1m", "dim": "\033[2m", "blue": "\033[34m", "green": "\033[32m", "yel": "\033[33m",
     "mag": "\033[35m", "r": "\033[0m"} if sys.stdout.isatty() else {k: "" for k in
                                                                       ("b", "dim", "blue", "green", "yel", "mag", "r")}


def _short(obj, n: int = 400) -> str:
    s = json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + " …"


def print_trace(res: AgentResult) -> None:
    print(f"{C['b']}AGENT TOOL EXECUTION{C['r']}")
    for ev in res.trace:
        d = ev.data
        if ev.kind == "llm_tool_selection":
            sel = ", ".join(f"{c['tool']}({_short(c['args'], 120)})" for c in d["calls"])
            print(f"  {C['mag']}LLM (step {d['step']}){C['r']}  selects -> {sel}")
        elif ev.kind == "tool_call":
            print(f"  {C['blue']}{d['path']}{C['r']}  {d['tool']}  {C['dim']}{_short(d['request'], 160)}{C['r']}")
            print(f"     input : {_short(d['input'], 200)}")
            print(f"     result: {_short(d['result'])}  {C['dim']}({d['ms']} ms){C['r']}")
        elif ev.kind == "payment_confirmation":
            print(f"  {C['yel']}CONFIRMATION{C['r']}  confirmed={d['confirmed']}  {_short(d['summary'], 200)}")
    print(f"{C['b']}AI ASSISTANT RESPONSE{C['r']}\n{C['green']}{res.answer}{C['r']}\n")


def confirm_on_terminal(summary: dict) -> bool:
    print(f"\n{C['yel']}{C['b']}PAYMENT CONFIRMATION REQUIRED{C['r']}")
    print(f"  Student : {summary['studentNo']}\n  Term    : {summary['term']}")
    print(f"  Amount  : {summary['amount']} {summary['currency']}")
    print(f"  Current balance : {summary['currentBalance']} {summary['currency']}")
    print(f"  Balance after   : {summary['balanceAfterPayment']} {summary['currency']}")
    answer = input("Type 'yes' to submit this payment, anything else to cancel: ").strip().lower()
    return answer in {"yes", "y", "evet", "e"}


async def main_async(args) -> None:
    gateway = GatewayClient(args.gateway)
    try:
        username = args.username or input("Username (e.g. s1001): ").strip()
        password = args.password or os.getenv("STUDENT_PASSWORD") or getpass.getpass("Password: ")
        session = await gateway.login(username, password)
        llm = GeminiLLM(model=args.model)
        mcp = PolicyMcpClient(args.mcp)
        agent = TuitionAgent(llm, mcp, gateway, session, confirm_on_terminal, trace_dir=args.trace_dir)
        print(f"Logged in as {session.username} (student {session.student_no}). LLM: {llm.name}")
        print(f"Gateway: {gateway.base_url} | MCP: {mcp.url} | traces -> {args.trace_dir}\n")

        if args.demo:
            for label, q in DEMO_QUESTIONS:
                print(f"{C['b']}=== {label} ==={C['r']}\n{C['b']}STUDENT:{C['r']} {q}")
                print_trace(await agent.ask(q))
        elif args.question:
            print(f"{C['b']}STUDENT:{C['r']} {args.question}")
            print_trace(await agent.ask(args.question))
        else:
            print("Ask about tuition (empty line or 'exit' to quit).")
            while True:
                try:
                    q = input(f"{C['b']}You:{C['r']} ").strip()
                except EOFError:
                    break
                if not q or q.lower() in {"exit", "quit"}:
                    break
                print_trace(await agent.ask(q))
        print(f"{C['dim']}LLM tokens this session: {agent.usage}{C['r']}")
    finally:
        await gateway.aclose()


def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    p = argparse.ArgumentParser(description="AI Tuition Assistant (CLI)")
    p.add_argument("--gateway", default=os.getenv("GATEWAY_URL", "http://localhost:8000"))
    p.add_argument("--mcp", default=os.getenv("MCP_URL", f"http://localhost:{os.getenv('MCP_HOST_PORT', '8001')}/mcp"))
    p.add_argument("--model", default=os.getenv("GEMINI_MODEL"))
    p.add_argument("--username", default=os.getenv("STUDENT_USERNAME"))
    p.add_argument("--password", default=None)
    p.add_argument("--trace-dir", default=os.getenv("TRACE_DIR", "traces"))
    p.add_argument("--demo", action="store_true", help="run the demo questions")
    p.add_argument("-q", "--question")
    try:
        asyncio.run(main_async(p.parse_args()))
    except (LLMError, GatewayError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except httpx.HTTPError:
        print("Error: Could not reach the gateway, MCP server or LLM provider. Check service URLs and connectivity.",
              file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
