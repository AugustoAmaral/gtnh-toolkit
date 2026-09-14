import io
import json

import requests

from gtnh_rag.ask import ask, build_messages, format_sources, stream_chat
from gtnh_rag.chunk import Chunk
from gtnh_rag.index import embed_missing, open_index, upsert_chunks
from gtnh_rag.search import Hit

HITS = [
    Hit("1", "wiki", "LFTR", "Usage", "LFTR > Usage\nBurns thorium salt.", 0.5),
    Hit("2", "quest", "Liquid Fluoride Thorium Reactor", "Nuclear Power", "Liquid Fluoride Thorium Reactor > Nuclear Power\nBuild it.", 0.4),
]

DOCS = [
    Chunk("wiki", "LFTR", "Usage", "LFTR > Usage\nThe Liquid Fluoride Thorium Reactor burns thorium salt for power."),
    Chunk("recipe", "Recipe: LFTR", "Helium", "Recipe: LFTR > Helium\n2x Thorium Dust -> 200 mB Helium | 2048 EU/t EV"),
]


def test_build_messages_numbers_excerpts_and_asks_for_pt_br():
    msgs = build_messages("como funciona o LFTR?", HITS)
    assert msgs[0]["role"] == "system" and "pt-BR" in msgs[0]["content"]
    user = msgs[1]["content"]
    assert "[1] LFTR > Usage\nBurns thorium salt." in user
    assert "[2] Liquid Fluoride Thorium Reactor > Nuclear Power\nBuild it." in user
    assert user.rstrip().endswith("como funciona o LFTR?")


def test_format_sources():
    assert format_sources(HITS) == "[1] wiki: LFTR > Usage\n[2] quest: Liquid Fluoride Thorium Reactor > Nuclear Power"


def test_stream_chat_yields_content_pieces():
    lines = [json.dumps({"message": {"content": "Olá"}, "done": False}), json.dumps({"message": {"content": " mundo"}, "done": True})]

    class Resp:
        status_code = 200
        ok = True

        def iter_lines(self):
            yield from (l.encode() for l in lines)

        def raise_for_status(self):
            pass

    class Session:
        def post(self, url, json=None, timeout=None, stream=None):
            assert url.endswith("/api/chat") and json["stream"] is True and json["model"] == "m"
            return Resp()

    assert "".join(stream_chat("http://h:11434", "m", [{"role": "user", "content": "x"}], session=Session())) == "Olá mundo"


def test_stream_chat_wraps_connection_errors():
    class Session:
        def post(self, url, json=None, timeout=None, stream=None):
            raise requests.ConnectionError("refused")

    try:
        list(stream_chat("http://h:11434", "m", [{"role": "user", "content": "x"}], session=Session()))
        assert False, "should have raised RuntimeError"
    except RuntimeError as e:
        assert "http://h:11434" in str(e) and "m" in str(e)


def test_stream_chat_surfaces_stream_error_line():
    class Resp:
        status_code = 200

        def iter_lines(self):
            yield json.dumps({"error": "model 'foo' not found"}).encode()

        def raise_for_status(self):
            pass

    class Session:
        def post(self, url, json=None, timeout=None, stream=None):
            return Resp()

    try:
        list(stream_chat("http://h:11434", "m", [{"role": "user", "content": "x"}], session=Session()))
        assert False, "should have raised RuntimeError"
    except RuntimeError as e:
        assert "model 'foo' not found" in str(e)


def test_stream_chat_http_error():
    class Resp:
        status_code = 500

        def iter_lines(self):
            return iter([])

        def raise_for_status(self):
            pass

    class Session:
        def post(self, url, json=None, timeout=None, stream=None):
            return Resp()

    try:
        list(stream_chat("http://h:11434", "m", [{"role": "user", "content": "x"}], session=Session()))
        assert False, "should have raised RuntimeError"
    except RuntimeError as e:
        assert "500" in str(e)


def test_ask_end_to_end(tmp_path, fake_embedder):
    conn = open_index(tmp_path / "i.sqlite")
    upsert_chunks(conn, DOCS)
    embed_missing(conn, fake_embedder)

    lines = [
        json.dumps({"message": {"content": "Olá"}, "done": False}),
        json.dumps({"message": {"content": " mundo"}, "done": True}),
    ]

    class Resp:
        status_code = 200

        def iter_lines(self):
            yield from (l.encode() for l in lines)

    class Session:
        def post(self, url, json=None, timeout=None, stream=None):
            return Resp()

    buf = io.StringIO()
    result = ask(conn, fake_embedder, "http://h:11434", "m", "thorium", k=2, out=buf, session=Session())

    assert result == "Olá mundo"
    text = buf.getvalue()
    assert "Olá mundo" in text
    assert "Fontes:" in text
    assert "[1]" in text
