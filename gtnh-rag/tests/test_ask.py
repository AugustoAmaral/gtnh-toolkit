import io
import json

import requests

from gtnh_rag.ask import ask, build_messages, format_sources, get_api_key, stream_chat
from gtnh_rag.chunk import Chunk
from gtnh_rag.index import embed_missing, open_index, upsert_chunks
from gtnh_rag.search import Hit

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
PROVIDER = ["parasail/fp8"]

HITS = [
    Hit("1", "wiki", "LFTR", "Usage", "LFTR > Usage\nBurns thorium salt.", 0.5),
    Hit("2", "quest", "Liquid Fluoride Thorium Reactor", "Nuclear Power", "Liquid Fluoride Thorium Reactor > Nuclear Power\nBuild it.", 0.4),
]

DOCS = [
    Chunk("wiki", "LFTR", "Usage", "LFTR > Usage\nThe Liquid Fluoride Thorium Reactor burns thorium salt for power."),
    Chunk("recipe", "Recipe: LFTR", "Helium", "Recipe: LFTR > Helium\n2x Thorium Dust -> 200 mB Helium | 2048 EU/t EV"),
]


def _sse(chunks):
    lines = [f"data: {json.dumps(c)}".encode() for c in chunks]
    lines.append(b"data: [DONE]")
    return lines


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
    lines = _sse([
        {"choices": [{"delta": {"content": "Olá"}}]},
        {"choices": [{"delta": {"content": " mundo"}}]},
    ])

    class Resp:
        status_code = 200

        def iter_lines(self):
            yield from lines

    class Session:
        def post(self, url, json=None, headers=None, timeout=None, stream=None):
            assert url == CHAT_URL
            assert json["stream"] is True and json["model"] == "m"
            assert json["provider"] == {"only": PROVIDER, "allow_fallbacks": False}
            assert headers["Authorization"] == "Bearer k"
            return Resp()

    result = "".join(stream_chat(CHAT_URL, "m", [{"role": "user", "content": "x"}], PROVIDER, "k", session=Session()))
    assert result == "Olá mundo"


def test_stream_chat_wraps_connection_errors():
    class Session:
        def post(self, url, json=None, headers=None, timeout=None, stream=None):
            raise requests.ConnectionError("refused")

    try:
        list(stream_chat(CHAT_URL, "m", [{"role": "user", "content": "x"}], PROVIDER, "k", session=Session()))
        assert False, "should have raised RuntimeError"
    except RuntimeError as e:
        assert CHAT_URL in str(e) and "m" in str(e)


def test_stream_chat_surfaces_stream_error_line():
    class Resp:
        status_code = 200

        def iter_lines(self):
            yield b'data: {"error": {"message": "model \'foo\' not found"}}'

    class Session:
        def post(self, url, json=None, headers=None, timeout=None, stream=None):
            return Resp()

    try:
        list(stream_chat(CHAT_URL, "m", [{"role": "user", "content": "x"}], PROVIDER, "k", session=Session()))
        assert False, "should have raised RuntimeError"
    except RuntimeError as e:
        assert "model 'foo' not found" in str(e)


def test_stream_chat_http_error():
    class Resp:
        status_code = 500

        def iter_lines(self):
            return iter([])

    class Session:
        def post(self, url, json=None, headers=None, timeout=None, stream=None):
            return Resp()

    try:
        list(stream_chat(CHAT_URL, "m", [{"role": "user", "content": "x"}], PROVIDER, "k", session=Session()))
        assert False, "should have raised RuntimeError"
    except RuntimeError as e:
        assert "500" in str(e)


def test_get_api_key_from_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")
    assert get_api_key() == "env-key"


def test_get_api_key_raises_without_env(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    try:
        get_api_key()
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "OPENROUTER_API_KEY" in str(exc)


def test_ask_end_to_end(tmp_path, fake_embedder):
    conn = open_index(tmp_path / "i.sqlite")
    upsert_chunks(conn, DOCS)
    embed_missing(conn, fake_embedder)

    lines = _sse([
        {"choices": [{"delta": {"content": "Olá"}}]},
        {"choices": [{"delta": {"content": " mundo"}}]},
    ])

    class Resp:
        status_code = 200

        def iter_lines(self):
            yield from lines

    class Session:
        def post(self, url, json=None, headers=None, timeout=None, stream=None):
            return Resp()

    buf = io.StringIO()
    result = ask(conn, fake_embedder, CHAT_URL, "m", PROVIDER, "thorium", k=2, out=buf, session=Session(), api_key="k")

    assert result == "Olá mundo"
    text = buf.getvalue()
    assert "Olá mundo" in text
    assert "Fontes:" in text
    assert "[1]" in text
