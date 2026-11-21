"""Checks the Gemini adapter builds valid google-genai requests (no network: the client is faked)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.genai import errors, types  # noqa: E402

from tuition_agent.agent import TOOLS  # noqa: E402
from tuition_agent.llm import GeminiLLM, LLMError, ToolCall  # noqa: E402


class FakeModels:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    async def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": list(contents), "config": config})
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, responses):
        self.aio = type("Aio", (), {})()
        self.aio.models = FakeModels(responses)


def _resp(parts):
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=parts))],
                                         usage_metadata=types.GenerateContentResponseUsageMetadata(
                                             prompt_token_count=100, candidates_token_count=20))


def test_gemini_session_function_calling_roundtrip():
    fc = types.FunctionCall(id="call-1", name="get_my_tuition", args={"term": "2026-FALL"})
    client = FakeClient([_resp([types.Part(function_call=fc)]), _resp([types.Part(text="Balance is 3000.00 USD.")])])
    llm = GeminiLLM.__new__(GeminiLLM)
    llm.client, llm.name = client, "gemini-test"
    session = llm.start("system", TOOLS)

    turn1 = asyncio.run(session.send_user("What is my balance?"))
    assert [(c.name, c.args) for c in turn1.calls] == [("get_my_tuition", {"term": "2026-FALL"})]
    assert turn1.usage == {"input_tokens": 100, "output_tokens": 20}

    cfg = client.aio.models.calls[0]["config"]
    assert cfg.automatic_function_calling.disable is True
    assert [d.name for d in cfg.tools[0].function_declarations] == [t.name for t in TOOLS]

    turn2 = asyncio.run(session.send_tool_results([(ToolCall("call-1", "get_my_tuition", {}), {"balance": 3000.0})]))
    assert turn2.text == "Balance is 3000.00 USD." and turn2.calls == []
    sent = client.aio.models.calls[1]["contents"]
    # user question, model function call (kept verbatim), our function response
    assert [c.role for c in sent] == ["user", "model", "user"]
    fr = sent[2].parts[0].function_response
    assert fr.name == "get_my_tuition" and fr.id == "call-1" and fr.response == {"result": {"balance": 3000.0}}


@pytest.mark.parametrize("status, expected", [
    (402, "credits are depleted"), (429, "quota/rate limit"),
    (401, "API key"), (403, "permissions"), (404, "model not found"), (503, "Try again later"),
])
def test_provider_errors_explain_next_step_without_leaking_request_details(status, expected):
    class FailingModels:
        async def generate_content(self, **kwargs):
            raise errors.APIError(status, {"error": {"code": status, "message": "private-request-detail"}})

    llm = GeminiLLM.__new__(GeminiLLM)
    llm.client = FakeClient([])
    llm.client.aio.models = FailingModels()
    llm.name = "gemini-test"
    with pytest.raises(LLMError) as exc:
        asyncio.run(llm.start("system", TOOLS).send_user("What is my balance?"))
    assert expected in str(exc.value) and f"HTTP {status}" in str(exc.value)
    assert "private-request-detail" not in str(exc.value)
