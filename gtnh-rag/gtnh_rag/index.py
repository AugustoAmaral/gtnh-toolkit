"""index.sqlite: chunks, FTS5 mirror and cached embeddings."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from pathlib import Path

import numpy as np

from .chunk import Chunk
from .embed import Embedder

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks(
  id TEXT PRIMARY KEY, source TEXT NOT NULL, doc_title TEXT NOT NULL, section TEXT NOT NULL, text TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  doc_title, section, text, content='chunks', content_rowid='rowid', tokenize='unicode61');
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, doc_title, section, text) VALUES (new.rowid, new.doc_title, new.section, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, doc_title, section, text) VALUES ('delete', old.rowid, old.doc_title, old.section, old.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, doc_title, section, text) VALUES ('delete', old.rowid, old.doc_title, old.section, old.text);
  INSERT INTO chunks_fts(rowid, doc_title, section, text) VALUES (new.rowid, new.doc_title, new.section, new.text);
END;
CREATE TABLE IF NOT EXISTS embeddings(
  chunk_id TEXT NOT NULL, model TEXT NOT NULL, vector BLOB NOT NULL, PRIMARY KEY(chunk_id, model));
"""


def open_index(path: Path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def upsert_chunks(conn: sqlite3.Connection, chunks: Iterable[Chunk]) -> tuple[int, int]:
    inserted = 0
    with conn:
        for c in chunks:
            cur = conn.execute(
                "INSERT OR IGNORE INTO chunks(id, source, doc_title, section, text) VALUES (?,?,?,?,?)",
                (c.id, c.source, c.doc_title, c.section, c.text),
            )
            inserted += cur.rowcount
    total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    return inserted, total


def count_by_source(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT source, COUNT(*) FROM chunks GROUP BY source").fetchall()
    return {source: count for source, count in rows}


def prune_missing(conn: sqlite3.Connection, keep_ids: set[str], sources: set[str] | None = None) -> int:
    rows = conn.execute("SELECT id, source FROM chunks").fetchall()
    if sources is None:
        stale = [i for i, s in rows if i not in keep_ids]
    else:
        stale = [i for i, s in rows if s in sources and i not in keep_ids]
    with conn:
        conn.executemany("DELETE FROM embeddings WHERE chunk_id=?", [(i,) for i in stale])
        conn.executemany("DELETE FROM chunks WHERE id=?", [(i,) for i in stale])
    return len(stale)


def embed_missing(conn: sqlite3.Connection, embedder: Embedder, batch: int = 64,
                  progress: Callable[[int, int], None] | None = None) -> int:
    rows = conn.execute(
        "SELECT c.id, c.text FROM chunks c LEFT JOIN embeddings e ON e.chunk_id=c.id AND e.model=? WHERE e.chunk_id IS NULL",
        (embedder.model,),
    ).fetchall()
    done = 0
    for i in range(0, len(rows), batch):
        part = rows[i:i + batch]
        vectors = embedder.embed([t for _, t in part])
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO embeddings(chunk_id, model, vector) VALUES (?,?,?)",
                [(cid, embedder.model, np.asarray(v, dtype=np.float32).tobytes()) for (cid, _), v in zip(part, vectors)],
            )
        done += len(part)
        if progress:
            progress(done, len(rows))
    return done


def load_matrix(conn: sqlite3.Connection, model: str) -> tuple[list[str], np.ndarray]:
    rows = conn.execute("SELECT chunk_id, vector FROM embeddings WHERE model=? ORDER BY chunk_id", (model,)).fetchall()
    if not rows:
        return [], np.zeros((0, 0), dtype=np.float32)
    ids = [r[0] for r in rows]
    mat = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return ids, (mat / norms).astype(np.float32)
