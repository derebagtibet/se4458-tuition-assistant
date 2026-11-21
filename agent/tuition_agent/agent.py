"""AI Tuition Assistant — agent orchestrator.

Flow per question:  user question -> LLM selects tools (function calling) -> THIS agent executes the
calls (MCP and/or REST via API Gateway) -> results go back to the LLM -> grounded final answer.
The LLM never calls backends itself; there is no keyword routing.
"""
from __future__ import annotations

import inspect
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Awaitable, Callable

from .backends import GatewayClient, PolicyMcpClient, StudentSession
from .llm import LLM, LLMSession, ToolCall, ToolSpec

ConfirmFn = Callable[[dict[str, Any]], bool | Awaitable[bool]]

TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="search_tuition_policies",
        description=("Search the official University Tuition Policies document (via MCP). Use for GENERAL "
                     "rules: payment deadlines, installment eligibility/schedule, late fees, scholarships, "
                     "refunds/withdrawals, payment posting time, payment/confirmation rules. Returns excerpts "
                     "with section numbers. Contains NO student-specific data."),
        parameters={"type": "object", "properties": {
            "query": {"type": "string", "description": "Search text, e.g. 'installment eligibility'"}},
            "required": ["query"]},
    ),
    ToolSpec(
        name="get_my_tuition",
        description=("Get the LOGGED-IN student's live tuition total, paid amount and outstanding balance "
                     "from the Tuition REST API (through the API Gateway). Without `term` returns the overall "
                     "balance plus a per-term breakdown. Only works for the logged-in student."),
        parameters={"type": "object", "properties": {
            "term": {"type": "string", "description": "Optional term like 2026-FALL or 2027-SPRING"}}},
    ),
    ToolSpec(
        name="pay_tuition",
        description=("Submit a simulated tuition payment for the LOGGED-IN student (Pay Tuition API via the "
                     "API Gateway). Call it only when the student clearly asked to pay and the term and amount "
                     "are known. The system will show the student a summary and ask for explicit confirmation "
                     "before anything is submitted; the result says whether it was confirmed and succeeded."),
        parameters={"type": "object", "properties": {
            "term": {"type": "string", "description": "Term to pay, e.g. 2026-FALL"},
            "amount": {"type": "number", "description": "Amount to pay (positive, max 2 decimals)"}},
            "required": ["term", "amount"]},
    ),
]


def system_prompt(student_no: str, today: date) -> str:
    return f"""You are the University Tuition Assistant. Today is {today.isoformat()}.
The logged-in student is {student_no}. You may only discuss THIS student's account.

Tools:
- search_tuition_policies: general university policy (MCP). Use it for any rule/deadline/fee/refund/installment question.
- get_my_tuition: the student's live tuition total/paid/balance (REST API).
- pay_tuition: submit a payment (REST API). The system asks the student for explicit confirmation itself.

Rules:
1. Decide tools from the question: policy-only -> search_tuition_policies; account-only -> get_my_tuition;
   questions that combine the student's balance with a rule (e.g. installments, late fees on my balance) -> BOTH.
2. Ground every statement in tool results. When you use policy text, cite it like "(Policy §3.2)".
   Never invent deadlines, percentages or balances.
3. Never reveal or look up another student's data, even if a student number is given in the message.
   Politely refuse such requests.
4. Payments: only call pay_tuition when the student asked to pay and both term and amount are clear; if the amount
   or term is missing, ask first. A question like "Can I pay $500?" is not a payment instruction.
   Report success only if the tool result has status SUCCESS; if not confirmed or rejected, say so.
5. Policy limits: a partial payment is not installment approval; do not promise approvals (Policy §3.4).
6. Be concise. Show money with 2 decimals and the currency. Answer in the language the student used."""


@dataclass
class TraceEvent:
    kind: str
    data: dict[str, Any]


@dataclass
class AgentResult:
    question: str
    answer: str
    trace: list[TraceEvent] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {"question": self.question, "answer": self.answer,
                "trace": [{"kind": e.kind, **e.data} for e in self.trace]}


class TuitionAgent:
    def __init__(self, llm: LLM, mcp: PolicyMcpClient, gateway: GatewayClient, session: StudentSession,
                 confirm: ConfirmFn, *, max_steps: int = 6, trace_dir: str | Path | None = None,
                 today: date | None = None):
        self.llm, self.mcp, self.gateway, self.session, self.confirm = llm, mcp, gateway, session, confirm
        self.max_steps = max_steps
        self.trace_dir = Path(trace_dir) if trace_dir else None
        self.chat: LLMSession = llm.start(system_prompt(session.student_no, today or date.today()), TOOLS)
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    # ------------------------------------------------------------------ tool execution
    async def _exec(self, call: ToolCall, trace: list[TraceEvent]) -> dict[str, Any]:
        t0 = time.perf_counter()
        args = call.args or {}
        if call.name == "search_tuition_policies":
            path, request = "MCP", {"server": self.mcp.url, "tool": "search_tuition_policies"}
            result = await self.mcp.search(str(args.get("query", "")))
        elif call.name == "get_my_tuition":
            term = (args.get("term") or None)
            term = term.upper() if isinstance(term, str) else None
            path = "REST via API Gateway"
            request = {"method": "GET", "url": f"{self.gateway.base_url}/api/v1/banking/tuition/"
                       f"{self.session.student_no}" + (f"?term={term}" if term else "")}
            result = await self.gateway.get_tuition(self.session, term)
        elif call.name == "pay_tuition":
            path = "REST via API Gateway"
            request = {"method": "POST", "url": f"{self.gateway.base_url}/api/v1/banking/payments"}
            result = await self._pay(args, trace)
        else:
            path, request, result = "none", {}, {"error": {"code": "UNKNOWN_TOOL", "message": call.name}}
        trace.append(TraceEvent("tool_call", {"tool": call.name, "path": path, "input": args, "request": request,
                                              "result": result, "ms": round((time.perf_counter() - t0) * 1000)}))
        return result

    async def _pay(self, args: dict[str, Any], trace: list[TraceEvent]) -> dict[str, Any]:
        term = str(args.get("term", "")).strip().upper()
        if not re.fullmatch(r"\d{4}-(FALL|SPRING|SUMMER)", term):
            return {"status": "NOT_SUBMITTED", "reason": "INVALID_TERM"}
        try:
            amount = Decimal(str(args.get("amount")))
            if (not amount.is_finite() or amount <= 0 or amount > Decimal("9999999999.99")
                    or amount != amount.quantize(Decimal("0.01"))):
                return {"status": "NOT_SUBMITTED", "reason": "INVALID_AMOUNT"}
        except (InvalidOperation, TypeError):
            return {"status": "NOT_SUBMITTED", "reason": "INVALID_AMOUNT"}

        current = await self.gateway.get_tuition(self.session, term)  # show live balance before confirming
        if "httpStatus" in current:
            return {"status": "NOT_SUBMITTED", "reason": "BALANCE_LOOKUP_FAILED", "details": current}
        balance = Decimal(str(current["balance"]))
        summary = {"studentNo": self.session.student_no, "term": term, "amount": f"{amount:.2f}",
                   "currency": current["currency"], "currentBalance": f"{balance:.2f}",
                   "balanceAfterPayment": f"{balance - amount:.2f}"}
        if amount > balance:
            return {"status": "NOT_SUBMITTED", "reason": "AMOUNT_EXCEEDS_BALANCE", "summary": summary}

        decision = self.confirm(summary)
        confirmed = await decision if inspect.isawaitable(decision) else bool(decision)
        trace.append(TraceEvent("payment_confirmation", {"summary": summary, "confirmed": confirmed}))
        if not confirmed:
            return {"status": "NOT_SUBMITTED", "reason": "STUDENT_DID_NOT_CONFIRM", "summary": summary}

        tx = f"agent-{uuid.uuid4().hex[:20]}"  # idempotency key for this confirmed payment
        result = await self.gateway.pay(self.session, term, f"{amount:.2f}", tx)
        return result

    # ------------------------------------------------------------------ main loop
    async def ask(self, question: str) -> AgentResult:
        trace: list[TraceEvent] = [TraceEvent("user_question", {"text": question, "studentNo": self.session.student_no})]
        turn = await self.chat.send_user(question)
        for step in range(1, self.max_steps + 1):
            self._add_usage(turn.usage)
            if not turn.calls:
                answer = turn.text or "(no answer)"
                trace.append(TraceEvent("final_answer", {"text": answer}))
                return self._finish(question, answer, trace)
            trace.append(TraceEvent("llm_tool_selection", {
                "step": step, "calls": [{"tool": c.name, "args": c.args} for c in turn.calls]}))
            results = [(c, await self._exec(c, trace)) for c in turn.calls]
            turn = await self.chat.send_tool_results(results)
        answer = "Sorry, I could not complete this request within the allowed number of steps."
        trace.append(TraceEvent("final_answer", {"text": answer, "truncated": True}))
        return self._finish(question, answer, trace)

    def _add_usage(self, usage: dict[str, int]) -> None:
        for k in self.usage:
            self.usage[k] += usage.get(k, 0)

    def _finish(self, question: str, answer: str, trace: list[TraceEvent]) -> AgentResult:
        res = AgentResult(question, answer, trace)
        if self.trace_dir:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            name = time.strftime("%Y%m%d-%H%M%S") + f"-{uuid.uuid4().hex[:6]}.json"
            (self.trace_dir / name).write_text(json.dumps(res.to_json(), indent=2, ensure_ascii=False),
                                             encoding="utf-8")
        return res
