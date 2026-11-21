"""Chunking + full-text (BM25) index for University_Tuition_Policies.md.

Indexing happens ONCE, ahead of queries:
  * `python -m policy_search.indexer build <policy.md> <index.json>` runs during the Docker build, and
  * the MCP server loads that JSON at start-up (it only builds in-process if the file is missing).
Queries never re-index the document.

Chunking strategy: one chunk per `###` subsection (e.g. "3.2 Number and size of installments").
`##` sections that have text before their first subsection (e.g. "1. Scope", "9. Guidance") and the
document preamble become their own chunks, so every sentence is searchable and carries a section ID.
"""
from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

DOCUMENT_NAME = "University_Tuition_Policies.md"

STOPWORDS = set("""a an and are as at be by can do does for from has have how i if in into is it its may me my
of on or our should so than that the their then there these this to was what when where which who why will
with within would you your am also any per not rule rules policy policies""".split())

# Tiny domain thesaurus so that natural phrasing still matches the policy wording.
SYNONYMS = {
    "due": ["deadline"], "deadline": ["due"], "instalment": ["installment"], "installment": ["instalment"],
    "pay": ["payment"], "payment": ["pay"], "late": ["overdue"], "overdue": ["late"],
    "penalty": ["fee", "late"], "discount": ["scholarship"], "scholarship": ["discount"],
    "withdraw": ["withdrawal", "refund"], "withdrawal": ["refund"], "money": ["refund"],
    "bank": ["banking"], "banking": ["bank"], "show": ["appear", "posting"], "reflect": ["appear", "posting"], "split": ["installment"], "plan": ["installment", "arrangement"],
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_H2 = re.compile(r"^##\s+(\d+)\.\s+(.*)$")
_H3 = re.compile(r"^###\s+(\d+\.\d+)\s+(.*)$")


def stem(tok: str) -> str:
    """Very small suffix stripper (enough for policy prose; avoids an NLP dependency)."""
    for suf, rep in (("ies", "y"), ("ments", "ment"), ("ings", ""), ("ing", ""), ("ed", ""), ("es", ""),
                     ("s", "")):
        if tok.endswith(suf) and len(tok) - len(suf) >= 4:
            return tok[: -len(suf)] + rep
    return tok


def tokenize(text: str, *, expand: bool = False) -> list[str]:
    toks = [t for t in _TOKEN_RE.findall(text.lower().replace("’", "'")) if t not in STOPWORDS]
    if expand:
        toks = toks + [s for t in toks for s in SYNONYMS.get(t, SYNONYMS.get(stem(t), []))]
    return [stem(t) for t in toks]


@dataclass
class Chunk:
    chunk_id: int
    section: str          # "3.2"  (or "1", "9", "0" for preamble)
    heading: str          # "Number and size of installments"
    parent_section: str   # "3. Installment policy"
    text: str


def split_markdown(md: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    section, heading, parent = "0", "Document overview", "University Tuition Policies"
    buf: list[str] = []

    def flush():
        body = "\n".join(buf).strip()
        if body:
            chunks.append(Chunk(len(chunks), section, heading, parent, body))
        buf.clear()

    for line in md.splitlines():
        if line.startswith("# ") and not line.startswith("## "):
            continue  # document title
        if m := _H2.match(line):
            flush()
            section, heading = m.group(1), m.group(2).strip()
            parent = f"{m.group(1)}. {m.group(2).strip()}"
            continue
        if m := _H3.match(line):
            flush()
            section, heading = m.group(1), m.group(2).strip()
            continue
        buf.append(line)
    flush()
    return chunks


def _clean(text: str) -> str:
    return re.sub(r"\*\*|`", "", text)


class PolicyIndex:
    """Two-field Okapi BM25 ("BM25F-lite"): one BM25 score over the chunk body and one over its title
    (subsection heading + parent section title), combined as body + HEADING_WEIGHT * title.
    Titles say what a chunk is ABOUT, while body text may mention a term only in passing
    (e.g. §2.1 mentions "installment arrangement" but is about the Fall deadline)."""

    HEADING_WEIGHT = 1.5

    def __init__(self, chunks: list[Chunk], document_id: str, k1: float = 1.5, b: float = 0.75):
        self.chunks, self.document_id, self.k1, self.b = chunks, document_id, k1, b
        self.fields = {
            "body": [Counter(tokenize(_clean(c.text))) for c in chunks],
            "title": [Counter(tokenize(f"{c.heading} {c.parent_section}")) for c in chunks],
        }
        self.df = {f: Counter(t for tf in tfs for t in tf) for f, tfs in self.fields.items()}
        self.avgdl = {f: (sum(sum(tf.values()) for tf in tfs) / len(tfs)) if tfs else 0.0
                      for f, tfs in self.fields.items()}

    # ---------------- build / persist
    @classmethod
    def from_markdown(cls, md: str) -> "PolicyIndex":
        m = re.search(r"\*\*Document ID:\*\*\s*([A-Z0-9-]+)", md)
        return cls(split_markdown(md), m.group(1) if m else "UNKNOWN")

    def to_json(self) -> dict:
        return {"document": DOCUMENT_NAME, "documentId": self.document_id, "k1": self.k1, "b": self.b,
                "chunks": [asdict(c) for c in self.chunks]}

    @classmethod
    def from_json(cls, data: dict) -> "PolicyIndex":
        return cls([Chunk(**c) for c in data["chunks"]], data["documentId"], data.get("k1", 1.5), data.get("b", 0.75))

    # ---------------- search
    def _bm25(self, field: str, i: int, q_terms: list[str]) -> float:
        tf, df, n = self.fields[field][i], self.df[field], len(self.chunks)
        dl, avgdl = sum(tf.values()), self.avgdl[field] or 1.0
        score = 0.0
        for t in set(q_terms):
            f = tf.get(t)
            if not f:
                continue
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            norm = self.k1 * (1 - self.b + self.b * dl / avgdl)
            score += idf * f * (self.k1 + 1) / (f + norm) * q_terms.count(t) ** 0.5
        return score

    def search(self, query: str, top_k: int = 3, min_score: float = 0.5) -> list[dict]:
        q_terms = tokenize(query, expand=True)
        scored: list[tuple[float, int]] = []
        for i, chunk in enumerate(self.chunks):
            score = self._bm25("body", i, q_terms) + self.HEADING_WEIGHT * self._bm25("title", i, q_terms)
            # FAQ entries mostly point to other sections; slightly prefer the normative text
            if chunk.section.startswith("8."):
                score *= 0.85
            if score >= min_score:
                scored.append((score, i))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [{"section": self.chunks[i].section, "heading": self.chunks[i].heading,
                 "parentSection": self.chunks[i].parent_section, "text": self.chunks[i].text,
                 "score": round(s, 3)} for s, i in scored[:top_k]]


def build_index_file(md_path: str | Path, out_path: str | Path) -> PolicyIndex:
    idx = PolicyIndex.from_markdown(Path(md_path).read_text(encoding="utf-8"))
    Path(out_path).write_text(json.dumps(idx.to_json(), indent=2, ensure_ascii=False), encoding="utf-8")
    return idx


def load_index(index_path: str | Path, md_path: str | Path) -> PolicyIndex:
    p = Path(index_path)
    if p.exists():
        return PolicyIndex.from_json(json.loads(p.read_text(encoding="utf-8")))
    return build_index_file(md_path, p)  # first start without a prebuilt index


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "build":
        print("usage: python -m policy_search.indexer build <policy.md> <index.json>")
        sys.exit(2)
    built = build_index_file(sys.argv[2], sys.argv[3])
    print(f"indexed {len(built.chunks)} chunks from {sys.argv[2]} -> {sys.argv[3]}")
