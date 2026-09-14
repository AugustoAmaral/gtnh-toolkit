import numpy as np
import requests

from gtnh_rag.chunk import Chunk
from gtnh_rag.embed import OllamaEmbedder
from gtnh_rag.index import count_by_source, embed_missing, load_matrix, open_index, prune_missing, upsert_chunks


def _chunks():
    return [
        Chunk("wiki", "LFTR", "Usage", "LFTR > Usage\nThe Liquid Fluoride Thorium Reactor burns thorium salt."),
        Chunk("wiki", "Bees", "Breeding", "Bees > Breeding\nImperial bees need jungle biomes."),
    ]


def test_upsert_is_idempotent_and_fts_searchable(tmp_path):
    conn = open_index(tmp_path / "i.sqlite")
    assert upsert_chunks(conn, _chunks()) == (2, 2)
    assert upsert_chunks(conn, _chunks()) == (0, 2)
    rows = conn.execute("SELECT doc_title FROM chunks_fts WHERE chunks_fts MATCH 'thorium'").fetchall()
    assert rows == [("LFTR",)]


def test_embed_missing_only_embeds_new_chunks(tmp_path, fake_embedder):
    conn = open_index(tmp_path / "i.sqlite")
    upsert_chunks(conn, _chunks())
    assert embed_missing(conn, fake_embedder) == 2
    assert embed_missing(conn, fake_embedder) == 0
    ids, mat = load_matrix(conn, "fake")
    assert len(ids) == 2 and mat.shape == (2, 64) and mat.dtype == np.float32
    assert np.allclose(np.linalg.norm(mat, axis=1), 1.0)


def test_prune_missing_removes_chunks_and_embeddings(tmp_path, fake_embedder):
    conn = open_index(tmp_path / "i.sqlite")
    chunks = _chunks()
    upsert_chunks(conn, chunks)
    embed_missing(conn, fake_embedder)
    assert prune_missing(conn, {chunks[0].id}) == 1
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'jungle'").fetchone()[0] == 0


def test_prune_missing_respects_sources(tmp_path):
    conn = open_index(tmp_path / "i.sqlite")
    chunks = _chunks()
    quest_chunk = Chunk("quest", chunks[1].doc_title, chunks[1].section, chunks[1].text)
    upsert_chunks(conn, [chunks[0], quest_chunk])
    assert prune_missing(conn, set(), sources={"wiki"}) == 1
    remaining = conn.execute("SELECT source FROM chunks").fetchall()
    assert remaining == [("quest",)]


def test_count_by_source(tmp_path):
    conn = open_index(tmp_path / "i.sqlite")
    chunks = _chunks()
    quest_chunk = Chunk("quest", chunks[1].doc_title, chunks[1].section, chunks[1].text)
    upsert_chunks(conn, [*chunks, quest_chunk])
    assert count_by_source(conn) == {"wiki": 2, "quest": 1}


def test_ollama_embedder_posts_and_reports_errors():
    calls = []

    class Resp:
        def __init__(self, ok, payload):
            self.ok, self._p = ok, payload
            self.status_code = 200 if ok else 500
            self.text = "boom"

        def json(self):
            return self._p

    class Session:
        def post(self, url, json=None, timeout=None):
            calls.append((url, json))
            if json["model"] == "bad":
                return Resp(False, {})
            return Resp(True, {"embeddings": [[0.1, 0.2]] * len(json["input"])})

    e = OllamaEmbedder("http://h:11434", "bge-m3", session=Session())
    assert e.embed(["a", "b"]) == [[0.1, 0.2], [0.1, 0.2]]
    assert calls[0] == ("http://h:11434/api/embed", {"model": "bge-m3", "input": ["a", "b"]})
    try:
        OllamaEmbedder("http://h:11434", "bad", session=Session()).embed(["x"])
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "http://h:11434" in str(exc) and "bad" in str(exc)


def test_update_trigger_keeps_fts_in_sync(tmp_path):
    conn = open_index(tmp_path / "i.sqlite")
    upsert_chunks(conn, _chunks())
    conn.execute("UPDATE chunks SET text=? WHERE doc_title='Bees'", ("Bees > Breeding\nNothing here",))
    conn.commit()
    rows_jungle = conn.execute("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'jungle'").fetchone()[0]
    rows_nothing = conn.execute("SELECT doc_title FROM chunks_fts WHERE chunks_fts MATCH 'nothing'").fetchall()
    assert rows_jungle == 0
    assert rows_nothing == [("Bees",)]


def test_ollama_embedder_wraps_connection_errors():
    class Session:
        def post(self, url, json=None, timeout=None):
            raise requests.ConnectionError("refused")

    try:
        OllamaEmbedder("http://h:11434", "bge-m3", session=Session()).embed(["x"])
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "http://h:11434" in str(exc) and "bge-m3" in str(exc)
