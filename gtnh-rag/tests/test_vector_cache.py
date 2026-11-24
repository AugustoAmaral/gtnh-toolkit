import json
import os
import sqlite3

import numpy as np
import pytest

import gtnh_rag.search as search_mod
from gtnh_rag import vector_cache
from gtnh_rag.chunk import Chunk
from gtnh_rag.cli import main
from gtnh_rag.index import embed_missing, embeddings_version, load_matrix, open_index, prune_missing, upsert_chunks
from gtnh_rag.search import _MatrixCache, load_vectors, search, vector_search

DOCS = [
    Chunk("wiki", "LFTR", "Usage", "LFTR > Usage\nThe Liquid Fluoride Thorium Reactor burns thorium salt for power."),
    Chunk("wiki", "Bees", "Breeding", "Bees > Breeding\nImperial bees need jungle biomes and royal jelly."),
    Chunk("recipe", "Recipe: LFTR", "Helium", "Recipe: LFTR > Helium\n2x Thorium Dust -> 200 mB Helium | 2048 EU/t EV"),
]
EXTRA = Chunk("wiki", "Plasma", "Usage", "Plasma > Usage\nHelium plasma feeds the plasma generator.")


def _index(tmp_path, embedder, docs=DOCS):
    db = tmp_path / "index.sqlite"
    conn = open_index(db)
    upsert_chunks(conn, docs)
    embed_missing(conn, embedder)
    return db, conn


def _fresh(db):
    """What a new `gtnh-rag search` process sees: a new connection and an empty in-process cache."""
    return open_index(db), _MatrixCache()


def _no_sqlite_load(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("matrix was rebuilt from sqlite instead of read from the disk cache")
    monkeypatch.setattr(search_mod, "load_matrix", boom)


def test_second_process_reads_matrix_from_disk(tmp_path, fake_embedder, monkeypatch):
    db, conn = _index(tmp_path, fake_embedder)
    first = vector_search(conn, fake_embedder, "jungle bees", cache=_MatrixCache())
    assert vector_cache.cache_path(db, "fake").exists()

    _no_sqlite_load(monkeypatch)
    conn2, cache2 = _fresh(db)
    assert vector_search(conn2, fake_embedder, "jungle bees", cache=cache2) == first


def test_cached_matrix_is_bit_identical_to_sqlite(tmp_path, fake_embedder):
    db, conn = _index(tmp_path, fake_embedder)
    load_vectors(conn, "fake", _MatrixCache())
    conn2, cache2 = _fresh(db)
    ids, mat = load_vectors(conn2, "fake", cache2)
    assert isinstance(mat, np.memmap)
    ref_ids, ref_mat = load_matrix(conn, "fake")
    assert ids == ref_ids
    assert mat.dtype == ref_mat.dtype and mat.shape == ref_mat.shape
    assert mat.tobytes() == ref_mat.tobytes()


def _assert_fresh_process_matches_sqlite(db, model="fake"):
    conn, cache = _fresh(db)
    ids, mat = load_vectors(conn, model, cache)
    ref_ids, ref_mat = load_matrix(conn, model)
    assert ids == ref_ids and mat.tobytes() == ref_mat.tobytes()
    return ids


def test_new_embedding_invalidates(tmp_path, fake_embedder):
    db, conn = _index(tmp_path, fake_embedder)
    load_vectors(conn, "fake", _MatrixCache())
    upsert_chunks(conn, [EXTRA])
    embed_missing(conn, fake_embedder)
    assert EXTRA.id in _assert_fresh_process_matches_sqlite(db)


def test_pruned_embedding_invalidates(tmp_path, fake_embedder):
    db, conn = _index(tmp_path, fake_embedder)
    load_vectors(conn, "fake", _MatrixCache())
    prune_missing(conn, {DOCS[0].id, DOCS[1].id})
    assert DOCS[2].id not in _assert_fresh_process_matches_sqlite(db)


def test_rewritten_vector_invalidates(tmp_path, fake_embedder):
    db, conn = _index(tmp_path, fake_embedder)
    load_vectors(conn, "fake", _MatrixCache())
    with conn:
        conn.execute("UPDATE embeddings SET vector=? WHERE chunk_id=?", (np.ones(64, np.float32).tobytes(), DOCS[0].id))
    _assert_fresh_process_matches_sqlite(db)


def test_swap_with_same_count_and_max_rowid_invalidates(tmp_path, fake_embedder):
    # A rebuild that prunes the newest chunk and embeds one new chunk: COUNT(*) and MAX(rowid)
    # come out exactly as before, which is all the old in-process cache key looked at.
    db, conn = _index(tmp_path, fake_embedder)
    load_vectors(conn, "fake", _MatrixCache())
    before = conn.execute("SELECT COUNT(*), MAX(rowid) FROM embeddings").fetchone()
    newest = conn.execute("SELECT chunk_id FROM embeddings ORDER BY rowid DESC LIMIT 1").fetchone()[0]
    prune_missing(conn, {d.id for d in DOCS} - {newest})
    upsert_chunks(conn, [EXTRA])
    embed_missing(conn, fake_embedder)
    assert conn.execute("SELECT COUNT(*), MAX(rowid) FROM embeddings").fetchone() == before
    ids = _assert_fresh_process_matches_sqlite(db)
    assert EXTRA.id in ids and newest not in ids


def test_unrelated_writes_keep_the_cache(tmp_path, fake_embedder, monkeypatch):
    db, conn = _index(tmp_path, fake_embedder)
    token = embeddings_version(conn, "fake")
    load_vectors(conn, "fake", _MatrixCache())

    class OtherModel(type(fake_embedder)):
        model = "other"

    upsert_chunks(conn, [EXTRA])          # new chunk, not embedded yet
    embed_missing(conn, OtherModel())      # embeddings for a different model
    assert embeddings_version(conn, "fake") == token

    _no_sqlite_load(monkeypatch)
    conn2, cache2 = _fresh(db)
    assert len(load_vectors(conn2, "fake", cache2)[0]) == 3


def test_legacy_index_gets_a_token_on_open(tmp_path, fake_embedder):
    # An index.sqlite from before embeddings_version existed: embeddings, but no version table.
    _, conn = _index(tmp_path / "a", fake_embedder)
    conn.executescript(
        "DROP TRIGGER embeddings_ver_ai; DROP TRIGGER embeddings_ver_ad; DROP TRIGGER embeddings_ver_au;"
        "DROP TABLE embeddings_version;"
    )
    conn.close()
    legacy = (tmp_path / "a" / "index.sqlite").read_bytes()
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "index.sqlite").write_bytes(legacy)

    tok_a = embeddings_version(open_index(tmp_path / "a" / "index.sqlite"), "fake")
    tok_b = embeddings_version(open_index(tmp_path / "b" / "index.sqlite"), "fake")
    assert tok_a and tok_b and tok_a != tok_b  # two copies of one legacy file never share a cache key


def test_restored_old_copy_does_not_reuse_cache(tmp_path, fake_embedder):
    db, conn = _index(tmp_path, fake_embedder)
    conn.close()
    backup = db.read_bytes()
    conn = open_index(db)
    load_vectors(conn, "fake", _MatrixCache())
    upsert_chunks(conn, [EXTRA])
    embed_missing(conn, fake_embedder)
    conn.close()
    assert EXTRA.id in _assert_fresh_process_matches_sqlite(db)  # disk cache now holds the 4-row index
    for suffix in ("-wal", "-shm"):
        (tmp_path / f"index.sqlite{suffix}").unlink(missing_ok=True)
    db.write_bytes(backup)
    assert EXTRA.id not in _assert_fresh_process_matches_sqlite(db)


@pytest.mark.parametrize("damage", ["truncate", "garbage", "wrong_model", "empty"])
def test_damaged_cache_file_is_rebuilt(tmp_path, fake_embedder, damage):
    db, conn = _index(tmp_path, fake_embedder)
    load_vectors(conn, "fake", _MatrixCache())
    path = vector_cache.cache_path(db, "fake")
    data = path.read_bytes()
    if damage == "truncate":
        path.write_bytes(data[:-10])
    elif damage == "garbage":
        path.write_bytes(b"GTNHVEC1" + b"\xff" * 40)
    elif damage == "wrong_model":
        path.write_bytes(data.replace(b'"model":"fake"', b'"model":"fakx"', 1))
    else:
        path.write_bytes(b"")
    _assert_fresh_process_matches_sqlite(db)
    assert vector_cache.read(path, "fake", embeddings_version(conn, "fake")) is not None  # rewritten


def test_unwritable_cache_warns_and_still_searches(tmp_path, fake_embedder, monkeypatch, capsys):
    db, conn = _index(tmp_path, fake_embedder)

    def fail(*a, **k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(vector_cache, "write", fail)
    ids = vector_search(conn, fake_embedder, "jungle bees", cache=_MatrixCache())
    assert ids[0] == DOCS[1].id
    assert "warning: could not write vector cache" in capsys.readouterr().err


def test_write_failure_leaves_no_temp_files(tmp_path):
    target = tmp_path / "index.sqlite.fake.vectors"
    target.mkdir()
    (target / "keep").write_text("x")  # os.replace onto a non-empty directory fails
    with pytest.raises(OSError):
        vector_cache.write(target, "fake", b"t", ["a", "b"], np.ones((2, 4), np.float32))
    assert [p.name for p in tmp_path.iterdir()] == [target.name]


def test_cache_file_respects_umask(tmp_path, fake_embedder):
    old = os.umask(0o022)
    try:
        db, conn = _index(tmp_path, fake_embedder)
        load_vectors(conn, "fake", _MatrixCache())
    finally:
        os.umask(old)
    assert vector_cache.cache_path(db, "fake").stat().st_mode & 0o777 == 0o644


def test_model_name_is_filesystem_safe(tmp_path):
    path = vector_cache.cache_path(tmp_path / "index.sqlite", "hf.co/BAAI/bge-m3:latest")
    assert path.parent == tmp_path and path.name == "index.sqlite.hf.co_BAAI_bge-m3_latest.vectors"


def test_in_memory_and_raw_connections_work_without_disk_cache(tmp_path, fake_embedder):
    mem = open_index(":memory:")
    upsert_chunks(mem, DOCS)
    embed_missing(mem, fake_embedder)
    assert vector_search(mem, fake_embedder, "jungle bees", cache=_MatrixCache())[0] == DOCS[1].id

    db, conn = _index(tmp_path, fake_embedder)
    conn.execute("DROP TABLE embeddings_version")  # like a connection to an index never opened by open_index
    raw = sqlite3.connect(db)
    assert vector_search(raw, fake_embedder, "jungle bees", cache=_MatrixCache())[0] == DOCS[1].id
    assert not vector_cache.cache_path(db, "fake").exists()


def test_empty_model_is_not_cached(tmp_path, fake_embedder):
    db = tmp_path / "index.sqlite"
    conn = open_index(db)
    upsert_chunks(conn, DOCS)
    assert vector_search(conn, fake_embedder, "x", cache=_MatrixCache()) == []
    assert not vector_cache.cache_path(db, "fake").exists()


def test_search_coverage_warning_uses_cached_count(tmp_path, fake_embedder, capsys, monkeypatch):
    db, conn = _index(tmp_path, fake_embedder)
    load_vectors(conn, "fake", _MatrixCache())
    upsert_chunks(conn, [EXTRA])  # chunk without an embedding: does not invalidate, but coverage drops
    _no_sqlite_load(monkeypatch)
    search_mod._cache.key = None
    search(open_index(db), fake_embedder, "thorium")
    assert "warning: vector index covers 3/4 chunks for model fake" in capsys.readouterr().err


def test_cli_search_twice_reuses_disk_cache(tmp_path, monkeypatch, capsys, fake_embedder):
    data = tmp_path / "data"
    (data / "raw" / "wiki").mkdir(parents=True)
    (data / "raw" / "wiki" / "LFTR.wikitext").write_text("== Usage ==\nThe Liquid Fluoride Thorium Reactor burns thorium salt.")
    cfg = tmp_path / "gtnh_rag.toml"
    cfg.write_text(f'data_dir = "{data}"\nembed_model = "fake"\n')
    monkeypatch.setattr("gtnh_rag.cli.make_embedder", lambda c, m=None, u=None: fake_embedder)
    assert main(["--config", str(cfg), "build"]) == 0
    capsys.readouterr()

    assert main(["--config", str(cfg), "search", "thorium", "--json"]) == 0
    first = capsys.readouterr().out
    assert (data / "index.sqlite.fake.vectors").exists()

    search_mod._cache.key = None  # a new process
    _no_sqlite_load(monkeypatch)
    assert main(["--config", str(cfg), "search", "thorium", "--json"]) == 0
    assert capsys.readouterr().out == first and json.loads(first)


# --- build warms the cache ---------------------------------------------------------------------

def _build_cfg(tmp_path, monkeypatch, fake_embedder, pages=("LFTR", "Fusion")):
    data = tmp_path / "data"
    wiki = data / "raw" / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    for name in pages:
        (wiki / f"{name}.wikitext").write_text(f"== Usage ==\nThe {name} burns thorium salt.")
    cfg = tmp_path / "gtnh_rag.toml"
    cfg.write_text(f'data_dir = "{data}"\nembed_model = "fake"\n')
    monkeypatch.setattr("gtnh_rag.cli.make_embedder", lambda c, m=None, u=None: fake_embedder)
    return cfg, data / "index.sqlite.fake.vectors"


def _file_id(path):
    st = path.stat()
    return st.st_ino, st.st_mtime_ns


def _load_matrix_spy(monkeypatch):
    calls = []
    real = search_mod.load_matrix
    monkeypatch.setattr(search_mod, "load_matrix", lambda *a, **k: calls.append(1) or real(*a, **k))
    return calls


def test_build_writes_cache_so_first_search_skips_sqlite(tmp_path, monkeypatch, capsys, fake_embedder):
    cfg, path = _build_cfg(tmp_path, monkeypatch, fake_embedder)
    assert main(["--config", str(cfg), "build"]) == 0
    assert "vector cache: written (2 vectors, fake)" in capsys.readouterr().out
    assert path.exists()

    search_mod._cache.key = None  # the search is a new process
    _no_sqlite_load(monkeypatch)
    assert main(["--config", str(cfg), "search", "thorium", "--json"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 2


def test_build_without_embedding_changes_does_not_rewrite(tmp_path, monkeypatch, capsys, fake_embedder):
    cfg, path = _build_cfg(tmp_path, monkeypatch, fake_embedder)
    assert main(["--config", str(cfg), "build"]) == 0
    first = _file_id(path)
    capsys.readouterr()

    search_mod._cache.key = None
    calls = _load_matrix_spy(monkeypatch)
    assert main(["--config", str(cfg), "build"]) == 0
    out = capsys.readouterr().out
    assert "embedded 0 chunks" in out and "vector cache: up to date (2 vectors, fake)" in out
    assert _file_id(path) == first and calls == []


def test_build_that_adds_embeddings_rewrites(tmp_path, monkeypatch, capsys, fake_embedder):
    cfg, path = _build_cfg(tmp_path, monkeypatch, fake_embedder)
    assert main(["--config", str(cfg), "build"]) == 0
    first = _file_id(path)
    (tmp_path / "data" / "raw" / "wiki" / "Naquadah.wikitext").write_text("== Usage ==\nNaquadah fuel.")
    capsys.readouterr()

    search_mod._cache.key = None
    assert main(["--config", str(cfg), "build"]) == 0
    assert "vector cache: written (3 vectors, fake)" in capsys.readouterr().out
    assert _file_id(path) != first
    search_mod._cache.key = None
    _no_sqlite_load(monkeypatch)
    assert main(["--config", str(cfg), "search", "Naquadah fuel", "--json"]) == 0
    assert "Naquadah" in {h["doc_title"] for h in json.loads(capsys.readouterr().out)}


def test_skip_embed_build_refreshes_cache_after_prune(tmp_path, monkeypatch, capsys, fake_embedder):
    cfg, path = _build_cfg(tmp_path, monkeypatch, fake_embedder)
    assert main(["--config", str(cfg), "build"]) == 0
    capsys.readouterr()

    search_mod._cache.key = None
    assert main(["--config", str(cfg), "build", "--skip-embed"]) == 0
    assert "vector cache: up to date (2 vectors, fake)" in capsys.readouterr().out

    (tmp_path / "data" / "raw" / "wiki" / "Fusion.wikitext").unlink()
    search_mod._cache.key = None
    assert main(["--config", str(cfg), "build", "--skip-embed"]) == 0
    assert "vector cache: written (1 vectors, fake)" in capsys.readouterr().out
    _assert_fresh_process_matches_sqlite(tmp_path / "data" / "index.sqlite")


def test_skip_embed_build_on_fresh_index_writes_nothing(tmp_path, monkeypatch, capsys, fake_embedder):
    cfg, path = _build_cfg(tmp_path, monkeypatch, fake_embedder)
    assert main(["--config", str(cfg), "build", "--skip-embed"]) == 0
    assert "vector cache" not in capsys.readouterr().out
    assert not path.exists()


def test_build_cache_write_failure_warns_and_never_claims_up_to_date(tmp_path, monkeypatch, capsys, fake_embedder):
    cfg, path = _build_cfg(tmp_path, monkeypatch, fake_embedder)
    assert main(["--config", str(cfg), "build"]) == 0  # leaves a cache file behind
    (tmp_path / "data" / "raw" / "wiki" / "Naquadah.wikitext").write_text("== Usage ==\nNaquadah fuel.")
    capsys.readouterr()

    def fail(*a, **k):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(vector_cache, "write", fail)
    search_mod._cache.key = None
    assert main(["--config", str(cfg), "build"]) == 0
    captured = capsys.readouterr()
    assert "warning: could not write vector cache" in captured.err
    assert "vector cache:" not in captured.out  # the old file is stale: neither "written" nor "up to date"


def test_build_warms_the_model_search_uses(tmp_path, monkeypatch, capsys, fake_embedder):
    cfg, path = _build_cfg(tmp_path, monkeypatch, fake_embedder)

    class Other(type(fake_embedder)):
        model = "other"

    monkeypatch.setattr("gtnh_rag.cli.make_embedder", lambda c, m=None, u=None: Other() if m == "other" else fake_embedder)
    assert main(["--config", str(cfg), "build", "--embed-model", "other"]) == 0
    out = capsys.readouterr().out
    assert "embedded 2 chunks with other" in out and "vector cache" not in out  # search model "fake" has none yet
    assert not path.exists() and not (path.parent / "index.sqlite.other.vectors").exists()
