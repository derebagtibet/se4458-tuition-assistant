"""Clients the agent uses to EXECUTE tool calls:
  * PolicyMcpClient -> MCP server (MCP client/server path, NOT through the API gateway)
  * GatewayClient   -> Tuition REST API, always through the API Gateway (Kong)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx


class PolicyMcpClient:
    def __init__(self, url: str, timeout: float = 20.0):
        self.url = url
        self.timeout = timeout

    async def list_tools(self) -> list[str]:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(self.url, timeout=self.timeout) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return [t.name for t in (await s.list_tools()).tools]

    async def search(self, query: str, top_k: int = 3) -> dict[str, Any]:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(self.url, timeout=self.timeout) as (r, w, _):
            async with ClientSession(r, w) as s:
                await s.initialize()
                res = await s.call_tool("search_tuition_policies", {"query": query, "top_k": top_k})
        if res.isError:
            return {"error": {"code": "MCP_TOOL_ERROR", "message": res.content[0].text if res.content else ""}}
        if res.structuredContent:
            return res.structuredContent.get("result", res.structuredContent)
        return json.loads(res.content[0].text)


@dataclass
class StudentSession:
    username: str
    student_no: str
    access_token: str


class GatewayError(Exception):
    pass


class GatewayClient:
    def __init__(self, base_url: str, timeout: float = 20.0, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _result(r: httpx.Response) -> dict[str, Any]:
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text}
        if r.is_success:
            return body
        # Error bodies are passed to the LLM so it can explain them (e.g. 409 duplicate, 429 limit).
        return {"httpStatus": r.status_code, **(body if isinstance(body, dict) else {"body": body})}

    async def login(self, username: str, password: str) -> StudentSession:
        r = await self._client.post("/api/v1/auth/token", json={"username": username, "password": password})
        if r.status_code != 200:
            raise GatewayError(f"Login failed ({r.status_code}): {r.text}")
        body = r.json()
        if body.get("role") != "student" or not body.get("studentNo"):
            raise GatewayError("The assistant is for student accounts only")
        return StudentSession(username=username, student_no=body["studentNo"], access_token=body["access_token"])

    async def get_tuition(self, session: StudentSession, term: str | None) -> dict[str, Any]:
        params = {"term": term} if term else None
        r = await self._client.get(f"/api/v1/banking/tuition/{session.student_no}", params=params,
                                   headers={"Authorization": f"Bearer {session.access_token}"})
        return self._result(r)

    async def pay(self, session: StudentSession, term: str, amount: str, transaction_id: str) -> dict[str, Any]:
        r = await self._client.post("/api/v1/banking/payments",
                                    headers={"Authorization": f"Bearer {session.access_token}"},
                                    json={"studentNo": session.student_no, "term": term, "amount": amount,
                                          "transactionId": transaction_id})
        return self._result(r)
