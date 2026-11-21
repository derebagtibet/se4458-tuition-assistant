"""Demonstrates the MCP server over the real MCP protocol (no LLM): lists tools, prints the tool schema
and runs three distinct retrieval queries. Use it for the "three MCP retrieval tests" evidence.

    pip install "mcp>=1.20,<2"
    python scripts/mcp_demo.py                                  # local: http://localhost:8001/mcp
    python scripts/mcp_demo.py https://<mcp-server-fqdn>/mcp    # deployed
"""
from __future__ import annotations

import asyncio
import json
import sys
import os
from pathlib import Path

from dotenv import load_dotenv

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

QUERIES = [
    "When is the Fall payment deadline?",
    "Can I pay my outstanding balance in installments?",
    "What is the late fee if I pay after the due date?",
]


async def main(url: str) -> None:
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            info = await session.initialize()
            print(f"Connected to MCP server '{info.serverInfo.name}' at {url}\n")
            for tool in (await session.list_tools()).tools:
                print(f"TOOL {tool.name}\n  inputSchema: {json.dumps(tool.inputSchema)}\n")
            for i, q in enumerate(QUERIES, 1):
                res = await session.call_tool("search_tuition_policies", {"query": q, "top_k": 2})
                data = res.structuredContent or json.loads(res.content[0].text)
                data = data.get("result", data)
                print(f"--- Retrieval test {i}: {q!r}")
                for m in data["matches"]:
                    text = " ".join(m["text"].split())
                    print(f"  §{m['section']} {m['heading']}  (score {m['score']})\n    {text[:160]}")
                print()


if __name__ == "__main__":
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    url = os.getenv("MCP_URL", f"http://localhost:{os.getenv('MCP_HOST_PORT', '8001')}/mcp")
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else url))
