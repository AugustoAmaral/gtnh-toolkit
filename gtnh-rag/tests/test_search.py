from gtnh_rag.chunk import Chunk
from gtnh_rag.index import embed_missing, open_index, upsert_chunks
from gtnh_rag.search import fts_query, fts_search, rrf, search, vector_search

DOCS = [
    Chunk("wiki", "LFTR", "Usage", "LFTR > Usage\nThe Liquid Fluoride Thorium Reactor burns thorium salt for power."),
    Chunk("wiki", "Bees", "Breeding", "Bees > Breeding\nImperial bees need jungle biomes and royal jelly."),
    Chunk("recipe", "Recipe: LFTR", "Helium", "Recipe: LFTR > Helium\n2x Thorium Dust -> 200 mB Helium | 2048 EU/t EV"),
]


def _index(tmp_path, embedder):
    conn = open_index(tmp_path / "i.sqlite")
    upsert_chunks(conn, DOCS)
    embed_missing(conn, embedder)
    return conn


def test_fts_query_keeps_phrases_and_ors_terms():
    assert fts_query('"Thorium Reactor" setup') == '"Thorium Reactor" OR "setup"'
    assert fts_query("a-b c:d (e)") == '"a" OR "b" OR "c" OR "d" OR "e"'


def test_fts_search_ranks_exact_name(tmp_path, fake_embedder):
    conn = _index(tmp_path, fake_embedder)
    ids = fts_search(conn, "thorium reactor")
    assert ids[0] == DOCS[0].id
    assert DOCS[1].id not in ids


def test_vector_search_returns_all_ranked(tmp_path, fake_embedder):
    conn = _index(tmp_path, fake_embedder)
    ids = vector_search(conn, fake_embedder, "jungle bees royal jelly")
    assert ids[0] == DOCS[1].id and len(ids) == 3


def test_rrf_fuses_rankings():
    fused = rrf([["a", "b", "c"], ["b", "a"]], k=1)
    assert [i for i, _ in fused] == ["a", "b", "c"]
    assert fused[0][1] == 1 / 2 + 1 / 3


def test_search_returns_hits_with_text(tmp_path, fake_embedder):
    conn = _index(tmp_path, fake_embedder)
    hits = search(conn, fake_embedder, "thorium", k=2)
    assert len(hits) == 2
    assert {h.doc_title for h in hits} == {"LFTR", "Recipe: LFTR"}
    assert hits[0].text.startswith(hits[0].doc_title)


def test_fts_search_survives_operator_words(tmp_path, fake_embedder):
    conn = _index(tmp_path, fake_embedder)
    assert isinstance(fts_search(conn, "OR gate"), list)
    assert isinstance(fts_search(conn, "NOT AND"), list)
    assert isinstance(fts_search(conn, "*"), list)
    hits = search(conn, fake_embedder, "OR thorium", k=2)
    assert hits[0].doc_title == "LFTR"


def test_vector_cache_refreshes_after_new_embeddings(tmp_path, fake_embedder):
    conn = open_index(tmp_path / "i.sqlite")
    upsert_chunks(conn, DOCS[:2])
    embed_missing(conn, fake_embedder)
    ids1 = vector_search(conn, fake_embedder, "jungle bees royal jelly")
    assert len(ids1) == 2
    upsert_chunks(conn, DOCS[2:])
    embed_missing(conn, fake_embedder)
    ids2 = vector_search(conn, fake_embedder, "thorium helium dust")
    assert len(ids2) == 3 and DOCS[2].id in ids2


def test_search_warns_on_partial_coverage(tmp_path, fake_embedder, capsys):
    conn = _index(tmp_path, fake_embedder)
    extra = Chunk("wiki", "Extra", "Section", "Extra > Section\nUnrelated text with no embedding yet.")
    upsert_chunks(conn, [extra])
    search(conn, fake_embedder, "thorium")
    err = capsys.readouterr().err
    assert "warning: vector index covers 3/4 chunks for model fake" in err


def test_search_no_warning_when_fully_covered(tmp_path, fake_embedder, capsys):
    conn = _index(tmp_path, fake_embedder)
    search(conn, fake_embedder, "thorium")
    err = capsys.readouterr().err
    assert err == ""
