"""MCP server (official MCP Python SDK, FastMCP) exposing the indexed tuition policy document.

Tool: search_tuition_policies(query, top_k=3) -> relevant excerpts with section references.
Transport: Streamable HTTP at http://<host>:<port>/mcp (default) or stdio (MCP_TRANSPORT=stdio).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from policy_search.indexer import DOCUMENT_NAME, load_index

HERE = Path(__file__).resolve().parent
POLICY_PATH = os.getenv("POLICY_PATH", str(HERE / "data" / DOCUMENT_NAME))
INDEX_PATH = os.getenv("INDEX_PATH", str(HERE / "data" / "policy_index.json"))

log = logging.getLogger("mcp-tuition-policies")

# Built/loaded once at process start — never per question.
INDEX = load_index(INDEX_PATH, POLICY_PATH)
log.info("Loaded policy index: %d chunks (%s)", len(INDEX.chunks), INDEX.document_id)

mcp = FastMCP(
    "tuition-policies",
    instructions="Search the University Tuition Policies document (deadlines, installments, late fees, "
                 "scholarships, refunds, banking/payment rules). Returns excerpts with section IDs. "
                 "It contains NO student-specific balances.",
    host=os.getenv("MCP_HOST", "0.0.0.0"),
    port=int(os.getenv("PORT", os.getenv("MCP_PORT", "8001"))),
    stateless_http=True,
    json_response=True,
    # Behind Azure Container Apps the Host header is the public FQDN; the service exposes only a public
    # read-only policy document, so DNS-rebinding protection is disabled (documented in README).
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


@mcp.tool()
def search_tuition_policies(query: str, top_k: int = 3) -> dict:
    """Search the university tuition POLICY document (general rules only, not student account data).

    Use for questions about payment deadlines, installment eligibility and schedule, late fees,
    scholarships, refunds/withdrawals, payment posting time, and payment/banking rules.

    Args:
        query: natural-language search text, e.g. "installment eligibility" or "fall payment deadline".
        top_k: number of excerpts to return (1-5).
    Returns:
        {"document", "documentId", "query", "matches": [{"section", "heading", "parentSection", "text", "score"}]}
    """
    top_k = max(1, min(int(top_k), 5))
    matches = INDEX.search(query, top_k=top_k)
    result = {"document": DOCUMENT_NAME, "documentId": INDEX.document_id, "query": query, "matches": matches}
    if not matches:
        result["note"] = "No relevant policy section found."
    return result


@mcp.resource("policy://tuition/document")
def policy_document() -> str:
    """Full text of the indexed tuition policy document."""
    return Path(POLICY_PATH).read_text(encoding="utf-8")


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "chunks": len(INDEX.chunks), "documentId": INDEX.document_id})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mcp.run(transport=os.getenv("MCP_TRANSPORT", "streamable-http"))
