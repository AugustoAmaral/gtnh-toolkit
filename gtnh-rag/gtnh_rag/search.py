"""Hybrid retrieval: FTS5 BM25 + cosine over cached embeddings, fused with RRF."""
from __future__ import annotations

import re
import sqlite3
import sys
from dataclasses import dataclass

import numpy as np

from .embed import Embedder
from .index import load_matrix

_PHRASE = re.compile(r'"([^"]+)"')
_TOKEN = re.compile(r"[\w]+", re.UNICODE)


@dataclass
class Hit:
    id: str
    source: str
    doc_title: str
    section: str
    text: str
    score: float


def fts_query(query: str) -> str:
    parts: list[str] = []
    for m in _PHRASE.finditer(query):
        parts.append(f'"{m.group(1)}"')
    rest = _PHRASE.sub(" ", query)
    parts.extend(f'"{token}"' for token in _TOKEN.findall(rest))
    return " OR ".join(parts) if parts else '""'


def fts_search(conn: sqlite3.Connection, query: str, n: int = 50) -> list[str]:
    q = fts_query(query)
    rows = conn.execute(
        "SELECT c.id FROM chunks_fts f JOIN chunks c ON c.rowid=f.rowid WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts, 3.0, 2.0, 1.0) LIMIT ?",
        (q, n),
    ).fetchall()
    return [r[0] for r in rows]


class _MatrixCache:
    def __init__(self):
        self.key = None
        self.value = None


_cache = _MatrixCache()


def vector_search(conn: sqlite3.Connection, embedder: Embedder, query: str, n: int = 50, cache=_cache) -> list[str]:
    count, max_rowid = conn.execute(
        "SELECT COUNT(*), COALESCE(MAX(rowid), 0) FROM embeddings WHERE model=?", (embedder.model,)
    ).fetchone()
    key = (id(conn), embedder.model, count, max_rowid)
    if cache.key != key:
        cache.key, cache.value = key, load_matrix(conn, embedder.model)
    ids, mat = cache.value
    if not ids:
        return []
    q = np.asarray(embedder.embed([query])[0], dtype=np.float32)
    norm = np.linalg.norm(q)
    q = q / norm if norm else q
    scores = mat @ q
    top = np.argsort(-scores)[:n]
    return [ids[i] for i in top]


def rrf(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking, start=1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def search(conn: sqlite3.Connection, embedder: Embedder, query: str, k: int = 10) -> list[Hit]:
    covered, total = conn.execute(
        "SELECT (SELECT COUNT(*) FROM embeddings WHERE model=?), (SELECT COUNT(*) FROM chunks)",
        (embedder.model,),
    ).fetchone()
    if covered < total:
        suffix = " (FTS-only)" if covered == 0 else ""
        print(f"warning: vector index covers {covered}/{total} chunks for model {embedder.model}{suffix}", file=sys.stderr)
    fused = rrf([fts_search(conn, query), vector_search(conn, embedder, query)])[:k]
    hits: list[Hit] = []
    for cid, score in fused:
        row = conn.execute("SELECT source, doc_title, section, text FROM chunks WHERE id=?", (cid,)).fetchone()
        if row:
            hits.append(Hit(cid, row[0], row[1], row[2], row[3], score))
    return hits
