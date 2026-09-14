"""Answer a question from retrieved chunks with an Ollama chat model."""
from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Iterator

import requests

from .embed import Embedder
from .search import Hit, search

SYSTEM_PROMPT = (
    "Você é um assistente do modpack GregTech: New Horizons (GTNH), versão 2.9.0-beta-2. "
    "Responda SEMPRE em pt-BR. Use apenas os trechos fornecidos; não invente números, receitas ou mecânicas. "
    "Cite cada afirmação com o número do trecho entre colchetes, como [1] ou [2][3]. "
    "Se os trechos não cobrem a pergunta, diga isso explicitamente e responda só o que estiver coberto. "
    "Nomes de itens, máquinas e fluidos ficam em inglês, como aparecem no jogo."
)


def build_messages(query: str, hits: list[Hit]) -> list[dict]:
    excerpts = "\n\n".join(f"[{i}] {h.text}" for i, h in enumerate(hits, start=1))
    user = f"Trechos:\n\n{excerpts}\n\nPergunta: {query}"
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def format_sources(hits: list[Hit]) -> str:
    return "\n".join(f"[{i}] {h.source}: {h.doc_title}" + (f" > {h.section}" if h.section else "") for i, h in enumerate(hits, start=1))


def stream_chat(url: str, model: str, messages: list[dict], session=None, timeout: int = 600) -> Iterator[str]:
    s = session or requests.Session()
    try:
        resp = s.post(f"{url.rstrip('/')}/api/chat", json={"model": model, "messages": messages, "stream": True}, timeout=timeout, stream=True)
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError(f"chat failed at {url} with model {model}: {exc}") from exc
    if getattr(resp, "status_code", 200) >= 400:
        raise RuntimeError(f"chat failed at {url} with model {model}: HTTP {resp.status_code}")
    for line in resp.iter_lines():
        if not line:
            continue
        try:
            data = json.loads(line)
        except ValueError as exc:
            raise RuntimeError(f"chat failed at {url} with model {model}: {exc}") from exc
        if data.get("error"):
            raise RuntimeError(f"chat failed at {url} with model {model}: {data['error']}")
        piece = data.get("message", {}).get("content", "")
        if piece:
            yield piece
        if data.get("done"):
            break


def ask(conn: sqlite3.Connection, embedder: Embedder, chat_url: str, model: str, query: str,
        k: int = 8, out=sys.stdout, session=None) -> str:
    hits = search(conn, embedder, query, k=k)
    if not hits:
        out.write("Nenhum trecho encontrado no índice.\n")
        return ""
    answer = []
    for piece in stream_chat(chat_url, model, build_messages(query, hits), session=session):
        out.write(piece)
        out.flush()
        answer.append(piece)
    out.write("\n\nFontes:\n" + format_sources(hits) + "\n")
    return "".join(answer)
