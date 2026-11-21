"""MCP server / indexing tests (no network, no LLM)."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from policy_search.indexer import PolicyIndex, build_index_file, load_index, split_markdown, tokenize  # noqa: E402

POLICY = ROOT / "data" / "University_Tuition_Policies.md"


@pytest.fixture(scope="module")
def index() -> PolicyIndex:
    return PolicyIndex.from_markdown(POLICY.read_text(encoding="utf-8"))


def test_chunking_one_chunk_per_subsection(index):
    sections = [c.section for c in index.chunks]
    assert {"2.1", "2.2", "2.3", "3.1", "3.2", "3.3", "3.4", "4.1", "6.1", "7.3", "9"} <= set(sections)
    assert len(sections) == len(set(sections)), "section IDs must be unique"
    c32 = next(c for c in index.chunks if c.section == "3.2")
    assert c32.heading == "Number and size of installments" and c32.parent_section == "3. Installment policy"
    assert index.document_id == "UNI-TUITION-2026-01"


def test_tokenizer_stems_and_drops_stopwords():
    assert tokenize("The Installments are due") == ["installment", "due"]


# ---- three (plus) distinct retrieval tests required by the assignment ----
def test_retrieval_payment_deadline(index):
    top = [m["section"] for m in index.search("When is the Fall payment deadline?")]
    assert top[0] in {"2.1", "8.1"} and "2.1" in top


def test_retrieval_installments(index):
    top = [m["section"] for m in index.search("Can I pay my outstanding balance in installments?")]
    assert {"3.1", "3.2"} & set(top)


def test_retrieval_late_fee(index):
    res = index.search("what is the late fee")
    assert res[0]["section"] == "4.1" and "2%" in res[0]["text"]


def test_retrieval_refund(index):
    assert index.search("refund if I withdraw early")[0]["section"] == "6.1"


def test_retrieval_payment_posting_via_synonym(index):
    assert index.search("how long until my payment shows up")[0]["section"] == "2.3"


def test_irrelevant_query_returns_nothing(index):
    assert index.search("pizza recipe with mozzarella") == []


def test_index_persisted_and_reloaded_without_reindexing(tmp_path, index):
    out = tmp_path / "idx.json"
    build_index_file(POLICY, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["documentId"] == "UNI-TUITION-2026-01" and len(data["chunks"]) == len(index.chunks)
    reloaded = load_index(out, "/does/not/matter.md")  # loads JSON, does not touch the markdown
    assert reloaded.search("late fee")[0]["section"] == "4.1"


def test_split_handles_text_before_first_subsection():
    chunks = split_markdown("# T\nintro\n## 1. A\nbody a\n### 1.1 Sub\nbody sub\n")
    assert [(c.section, c.text) for c in chunks] == [("0", "intro"), ("1", "body a"), ("1.1", "body sub")]


def test_mcp_tool_registered_and_returns_section_references(monkeypatch):
    monkeypatch.setenv("INDEX_PATH", str(ROOT / "data" / "policy_index.json"))
    import server  # noqa: WPS433

    async def run():
        tools = {t.name: t for t in await server.mcp.list_tools()}
        assert "search_tuition_policies" in tools
        assert "query" in tools["search_tuition_policies"].inputSchema["properties"]
        return await server.mcp.call_tool("search_tuition_policies", {"query": "installment schedule", "top_k": 2})

    result = asyncio.run(run())
    content = result[0] if isinstance(result, tuple) else result
    payload = json.loads(content[0].text)
    assert payload["document"] == "University_Tuition_Policies.md"
    assert len(payload["matches"]) == 2 and payload["matches"][0]["section"] == "3.3"
