"""Agent orchestration tests with a scripted LLM, a fake MCP client and a mocked gateway
(no network, no live LLM, no cloud)."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tuition_agent.agent import TOOLS, TuitionAgent  # noqa: E402
from tuition_agent.backends import GatewayClient, StudentSession  # noqa: E402
from tuition_agent.llm import LLMTurn, ScriptedLLM, ToolCall  # noqa: E402

SESSION = StudentSession(username="s1001", student_no="S1001", access_token="tok-s1001")


class FakeMcp:
    url = "http://mcp.test/mcp"

    def __init__(self):
        self.queries: list[str] = []

    async def search(self, query: str, top_k: int = 3):
        self.queries.append(query)
        return {"document": "University_Tuition_Policies.md", "matches": [
            {"section": "3.2", "heading": "Number and size of installments",
             "text": "An approved balance may be divided into up to three installments."}]}


class FakeGateway:
    """Records requests; behaves like the Tuition API for S1001 (balance 3000 in 2026-FALL)."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.balance = 3000.0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["Authorization"] == "Bearer tok-s1001"
        if request.method == "GET":
            return httpx.Response(200, json={"studentNo": "S1001", "scope": "TERM", "term": "2026-FALL",
                                             "currency": "USD", "tuitionTotal": 3000.0,
                                             "paidAmount": 3000.0 - self.balance, "balance": self.balance,
                                             "terms": []})
        body = json.loads(request.content)
        self.balance -= float(body["amount"])
        return httpx.Response(201, json={"status": "SUCCESS", "transactionId": body["transactionId"],
                                         "studentNo": body["studentNo"], "term": body["term"], "currency": "USD",
                                         "paidAmount": float(body["amount"]), "totalPaid": 3000.0 - self.balance,
                                         "remainingBalance": self.balance})


@pytest.fixture()
def gw():
    return FakeGateway()


def make_agent(turns, gw, confirm=lambda s: True, **kw):
    llm = ScriptedLLM(turns)
    client = GatewayClient("http://gateway.test", transport=httpx.MockTransport(gw.handler))
    mcp = FakeMcp()
    return TuitionAgent(llm, mcp, client, SESSION, confirm, **kw), llm, mcp


def run(coro):
    return asyncio.run(coro)


def tool_paths(res):
    return [(e.data["tool"], e.data["path"]) for e in res.trace if e.kind == "tool_call"]


def test_tools_exposed_to_llm_have_no_student_number_parameter():
    for t in TOOLS:
        assert "studentNo" not in t.parameters.get("properties", {})


def test_mcp_only_question(gw):
    agent, llm, mcp = make_agent([
        LLMTurn(None, [ToolCall("1", "search_tuition_policies", {"query": "fall payment deadline"})]),
        LLMTurn("The Fall deadline is September 30, 2026 (Policy §2.1)."),
    ], gw)
    res = run(agent.ask("When is the payment deadline?"))
    assert tool_paths(res) == [("search_tuition_policies", "MCP")]
    assert gw.requests == [] and mcp.queries == ["fall payment deadline"]
    assert "§2.1" in res.answer
    # the tool result was returned to the LLM for grounding
    assert llm.seen_results[0][0][1]["matches"][0]["section"] == "3.2"


def test_rest_only_question_uses_session_identity(gw):
    agent, _, _ = make_agent([
        LLMTurn(None, [ToolCall("1", "get_my_tuition", {})]),
        LLMTurn("Your balance is 3000.00 USD."),
    ], gw)
    res = run(agent.ask("What is my balance?"))
    assert tool_paths(res) == [("get_my_tuition", "REST via API Gateway")]
    assert str(gw.requests[0].url) == "http://gateway.test/api/v1/banking/tuition/S1001"


def test_combined_mcp_and_rest(gw):
    agent, llm, _ = make_agent([
        LLMTurn(None, [ToolCall("1", "get_my_tuition", {"term": "2026-fall"}),
                       ToolCall("2", "search_tuition_policies", {"query": "installment rules"})]),
        LLMTurn("Balance 3000.00 USD; up to three installments (Policy §3.2)."),
    ], gw)
    res = run(agent.ask("Can I pay my outstanding balance in installments?"))
    assert sorted(p for _, p in tool_paths(res)) == ["MCP", "REST via API Gateway"]
    assert gw.requests[0].url.params["term"] == "2026-FALL"
    assert len(llm.seen_results[0]) == 2


def test_prompt_supplied_student_number_is_ignored(gw):
    agent, _, _ = make_agent([
        LLMTurn(None, [ToolCall("1", "get_my_tuition", {"studentNo": "S1002"})]),  # model tries anyway
        LLMTurn("I can only show your own account."),
    ], gw)
    run(agent.ask("What is the balance of S1002?"))
    assert all("S1002" not in str(r.url) for r in gw.requests)


def test_payment_requires_confirmation_and_is_not_sent_when_declined(gw):
    seen = []
    agent, llm, _ = make_agent([
        LLMTurn(None, [ToolCall("1", "pay_tuition", {"term": "2026-FALL", "amount": 500})]),
        LLMTurn("Payment cancelled; nothing was charged."),
    ], gw, confirm=lambda s: seen.append(s) or False)
    res = run(agent.ask("Pay 500 of my balance"))
    assert seen and seen[0]["amount"] == "500.00" and seen[0]["currentBalance"] == "3000.00"
    assert [r.method for r in gw.requests] == ["GET"]  # balance looked up, payment NOT posted
    assert llm.seen_results[0][0][1]["status"] == "NOT_SUBMITTED"
    assert any(e.kind == "payment_confirmation" and e.data["confirmed"] is False for e in res.trace)


def test_confirmed_payment_posts_once_with_idempotency_key(gw):
    agent, llm, _ = make_agent([
        LLMTurn(None, [ToolCall("1", "pay_tuition", {"term": "2026-FALL", "amount": "500"})]),
        LLMTurn("Paid 500.00 USD; remaining 2500.00 USD."),
    ], gw, confirm=lambda s: True)
    run(agent.ask("Pay 500 of my 2026-FALL balance"))
    posts = [r for r in gw.requests if r.method == "POST"]
    assert len(posts) == 1
    body = json.loads(posts[0].content)
    assert body["studentNo"] == "S1001" and body["amount"] == "500.00" and body["transactionId"].startswith("agent-")
    assert llm.seen_results[0][0][1]["status"] == "SUCCESS"


def test_async_confirm_callback_supported(gw):
    async def confirm(_):
        return True
    agent, _, _ = make_agent([
        LLMTurn(None, [ToolCall("1", "pay_tuition", {"term": "2026-FALL", "amount": 10})]),
        LLMTurn("done"),
    ], gw, confirm=confirm)
    run(agent.ask("pay 10"))
    assert any(r.method == "POST" for r in gw.requests)


def test_payment_above_balance_is_not_confirmed_or_sent(gw):
    asked = []
    agent, llm, _ = make_agent([
        LLMTurn(None, [ToolCall("1", "pay_tuition", {"term": "2026-FALL", "amount": 5000})]),
        LLMTurn("That exceeds your balance."),
    ], gw, confirm=lambda s: asked.append(s) or True)
    run(agent.ask("pay 5000"))
    assert asked == [] and all(r.method == "GET" for r in gw.requests)
    assert llm.seen_results[0][0][1]["reason"] == "AMOUNT_EXCEEDS_BALANCE"


def test_invalid_amount_rejected_without_backend_call(gw):
    agent, llm, _ = make_agent([
        LLMTurn(None, [ToolCall("1", "pay_tuition", {"term": "2026-FALL", "amount": -3})]),
        LLMTurn("invalid"),
    ], gw)
    run(agent.ask("pay -3"))
    assert gw.requests == [] and llm.seen_results[0][0][1]["reason"] == "INVALID_AMOUNT"


@pytest.mark.parametrize("amount", ["10.001", "NaN", "sNaN", "Infinity", "-Infinity", "10000000000"])
def test_invalid_payment_amount_is_never_rounded_confirmed_or_sent(gw, amount):
    confirmations = []
    agent, llm, _ = make_agent([
        LLMTurn(None, [ToolCall("1", "pay_tuition", {"term": "2026-FALL", "amount": amount})]),
        LLMTurn("Invalid amount."),
    ], gw, confirm=lambda s: confirmations.append(s) or True)
    run(agent.ask(f"pay {amount}"))
    assert gw.requests == [] and confirmations == []
    assert llm.seen_results[0][0][1]["reason"] == "INVALID_AMOUNT"


@pytest.mark.parametrize("term", ["", "fall", "2026-WINTER"])
def test_payment_requires_valid_term_before_backend_lookup(gw, term):
    agent, llm, _ = make_agent([
        LLMTurn(None, [ToolCall("1", "pay_tuition", {"term": term, "amount": 10})]),
        LLMTurn("Please specify the term."),
    ], gw)
    run(agent.ask("pay 10"))
    assert gw.requests == []
    assert llm.seen_results[0][0][1]["reason"] == "INVALID_TERM"


def test_step_limit_stops_runaway_tool_loops(gw):
    loop = [LLMTurn(None, [ToolCall(str(i), "search_tuition_policies", {"query": "x"})]) for i in range(10)]
    agent, _, _ = make_agent(loop, gw, max_steps=3)
    res = run(agent.ask("loop"))
    assert res.trace[-1].data.get("truncated") is True
    assert sum(1 for e in res.trace if e.kind == "tool_call") == 3


def test_trace_written_to_disk(gw, tmp_path):
    agent, _, _ = make_agent([
        LLMTurn(None, [ToolCall("1", "get_my_tuition", {})]), LLMTurn("Ödeme bilgisi 🎓"),
    ], gw, trace_dir=tmp_path)
    run(agent.ask("balance?"))
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["answer"] == "Ödeme bilgisi 🎓" and [e["kind"] for e in data["trace"]] == [
        "user_question", "llm_tool_selection", "tool_call", "final_answer"]


def test_gateway_error_is_passed_to_llm(gw):
    def handler(request):
        return httpx.Response(403, json={"error": {"code": "FORBIDDEN", "message": "no"}})
    llm = ScriptedLLM([LLMTurn(None, [ToolCall("1", "get_my_tuition", {})]), LLMTurn("not allowed")])
    client = GatewayClient("http://gateway.test", transport=httpx.MockTransport(handler))
    agent = TuitionAgent(llm, FakeMcp(), client, SESSION, lambda s: True)
    run(agent.ask("balance"))
    assert llm.seen_results[0][0][1]["httpStatus"] == 403
