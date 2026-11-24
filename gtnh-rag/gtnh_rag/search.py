"""Hybrid retrieval: FTS5 BM25 + cosine over cached embeddings, fused with RRF."""
from __future__ import annotations

import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import vector_cache
from .embed import Embedder
from .index import embeddings_version, load_matrix

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


def _db_path(conn: sqlite3.Connection) -> Path | None:
    for _, name, file in conn.execute("PRAGMA database_list"):
        if name == "main":
            return Path(file) if file else None
    return None


def load_vectors(conn: sqlite3.Connection, model: str, cache=_cache) -> tuple[list[str], np.ndarray]:
    """``load_matrix(conn, model)``, memoized in-process and on disk next to the index file.

    Both layers are keyed by the index's ``embeddings_version`` token, so they are reused until an
    embedding of ``model`` is added, replaced or deleted. The on-disk copy is what makes repeated
    ``gtnh-rag search`` processes cheap; failing to write it only costs speed.
    """
    db = _db_path(conn)
    began = not conn.in_transaction
    if began:
        conn.execute("BEGIN")  # one read snapshot: the token must describe the rows we load
    try:
        token = embeddings_version(conn, model)
        if token is None:  # no version table (not opened via open_index): nothing to key a cache on
            return load_matrix(conn, model)
        key = (str(db) if db else id(conn), model, token)
        if cache.key == key:
            return cache.value
        path = vector_cache.cache_path(db, model) if db else None
        value = vector_cache.read(path, model, token) if path else None
        if value is None:
            value = load_matrix(conn, model)
            if path and value[0]:
                try:
                    vector_cache.write(path, model, token, *value)
                except OSError as exc:
                    print(f"warning: could not write vector cache {path}: {exc}", file=sys.stderr)
    finally:
        if began:
            conn.execute("COMMIT")
    cache.key, cache.value = key, value
    return value


def vector_search(conn: sqlite3.Connection, embedder: Embedder, query: str, n: int = 50, cache=_cache) -> list[str]:
    ids, mat = load_vectors(conn, embedder.model, cache)
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
    covered = len(load_vectors(conn, embedder.model)[0])
    total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
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
