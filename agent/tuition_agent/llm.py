"""LLM abstraction. The agent talks to an `LLMSession`; `GeminiLLM` is the real implementation
(Gemini function calling), `ScriptedLLM` (tests) replays pre-defined tool calls without a network."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str | None
    name: str
    args: dict[str, Any]


@dataclass
class LLMTurn:
    text: str | None
    calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


class LLMSession(Protocol):
    async def send_user(self, text: str) -> LLMTurn: ...
    async def send_tool_results(self, results: list[tuple[ToolCall, dict[str, Any]]]) -> LLMTurn: ...


class LLM(Protocol):
    name: str
    def start(self, system_prompt: str, tools: list[ToolSpec]) -> LLMSession: ...


class LLMError(RuntimeError):
    """Actionable provider/configuration error, without credential-bearing request details."""


# ----------------------------------------------------------------------------- Gemini
class GeminiLLM:
    """Google Gemini via the official `google-genai` SDK, using native function calling.
    Automatic function calling is DISABLED: the model only *selects* tools; our agent executes them."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        from google import genai  # imported lazily so unit tests don't need the SDK configured

        key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key:
            raise LLMError("Set GEMINI_API_KEY in the project .env or environment before running the agent.")
        self.client = genai.Client(api_key=key)
        self.name = model or os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

    def start(self, system_prompt: str, tools: list[ToolSpec]) -> "GeminiSession":
        return GeminiSession(self, system_prompt, tools)


class GeminiSession:
    def __init__(self, llm: GeminiLLM, system_prompt: str, tools: list[ToolSpec]):
        from google.genai import types

        self.types = types
        self.llm = llm
        self.contents: list[Any] = []
        self.config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.1,
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t.name, description=t.description,
                                          parameters_json_schema=t.parameters) for t in tools])],
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode="AUTO")),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    async def _generate(self) -> LLMTurn:
        from google.genai import errors

        try:
            resp = await self.llm.client.aio.models.generate_content(
                model=self.llm.name, contents=self.contents, config=self.config)
        except errors.APIError as exc:
            messages = {
                402: "Gemini project credits are depleted. Check AI Studio billing/credits or use a project with available quota.",
                429: "Gemini quota/rate limit exceeded. Wait for the quota to reset or check the project's limits.",
                401: "Gemini rejected the API key. Check GEMINI_API_KEY in .env or the environment.",
                403: "Gemini denied access. Check the API key and project permissions.",
                404: "Gemini model not found. Set GEMINI_MODEL to a model available to your project.",
            }
            raise LLMError(f"Gemini HTTP {exc.code}: " + messages.get(
                exc.code, "The provider request failed. Try again later.")) from exc
        cand = resp.candidates[0] if resp.candidates else None
        if cand is not None and cand.content is not None:
            # keep the model turn verbatim (preserves thought signatures required for multi-step calls)
            self.contents.append(cand.content)
        calls = [ToolCall(id=fc.id, name=fc.name, args=dict(fc.args or {})) for fc in (resp.function_calls or [])]
        text = None
        if not calls and cand is not None and cand.content is not None:
            text = "".join(p.text for p in (cand.content.parts or []) if getattr(p, "text", None)
                           and not getattr(p, "thought", False)).strip() or None
        u = resp.usage_metadata
        usage = {"input_tokens": getattr(u, "prompt_token_count", 0) or 0,
                 "output_tokens": (getattr(u, "candidates_token_count", 0) or 0)
                 + (getattr(u, "thoughts_token_count", 0) or 0)} if u else {}
        return LLMTurn(text=text, calls=calls, usage=usage)

    async def send_user(self, text: str) -> LLMTurn:
        t = self.types
        self.contents.append(t.Content(role="user", parts=[t.Part(text=text)]))
        return await self._generate()

    async def send_tool_results(self, results: list[tuple[ToolCall, dict[str, Any]]]) -> LLMTurn:
        t = self.types
        parts = [t.Part(function_response=t.FunctionResponse(id=call.id, name=call.name, response={"result": res}))
                 for call, res in results]
        self.contents.append(t.Content(role="user", parts=parts))
        return await self._generate()


# ----------------------------------------------------------------------------- test double
class ScriptedLLM:
    """Deterministic stand-in for unit tests: returns the queued turns in order and records what it saw."""

    name = "scripted"

    def __init__(self, turns: list[LLMTurn]):
        self.turns = list(turns)
        self.seen_user: list[str] = []
        self.seen_results: list[list[tuple[ToolCall, dict]]] = []
        self.system_prompt = ""
        self.tools: list[ToolSpec] = []

    def start(self, system_prompt: str, tools: list[ToolSpec]) -> "ScriptedLLM":
        self.system_prompt, self.tools = system_prompt, tools
        return self

    async def send_user(self, text: str) -> LLMTurn:
        self.seen_user.append(text)
        return self.turns.pop(0)

    async def send_tool_results(self, results):
        self.seen_results.append(results)
        return self.turns.pop(0)
