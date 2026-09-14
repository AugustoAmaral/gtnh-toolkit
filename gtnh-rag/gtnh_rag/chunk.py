"""Chunk data model and markdown section splitting."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


@dataclass(frozen=True)
class Chunk:
    source: str
    doc_title: str
    section: str
    text: str

    @property
    def id(self) -> str:
        key = f"{self.source}|{self.doc_title}|{self.section}|{self.text}".encode()
        return hashlib.sha256(key).hexdigest()[:16]

    @property
    def breadcrumb(self) -> str:
        return f"{self.doc_title} > {self.section}" if self.section else self.doc_title


def _sections(markdown: str) -> list[tuple[str, str]]:
    """Split markdown into (section title, body) pairs; the preamble has title ''."""
    out: list[tuple[str, list[str]]] = [("", [])]
    for line in markdown.splitlines():
        m = _HEADING.match(line)
        if m:
            out.append((m.group(2), []))
        else:
            out[-1][1].append(line)
    return [(title, "\n".join(lines).strip()) for title, lines in out]


def _pack_paragraphs(body: str, max_chars: int) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    pieces: list[str] = []
    current = ""
    for p in paragraphs:
        candidate = f"{current}\n\n{p}" if current else p
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            pieces.append(current)
            current = p
    if current:
        pieces.append(current)
    return pieces


def split_sections(markdown: str, doc_title: str, source: str, max_chars: int = 1500) -> list[Chunk]:
    """One chunk per heading; long sections are split on paragraph boundaries.

    Every chunk's text starts with its breadcrumb line so retrieval sees the context.
    """
    chunks: list[Chunk] = []
    for title, body in _sections(markdown):
        if not body:
            continue
        crumb = f"{doc_title} > {title}" if title else doc_title
        for piece in _pack_paragraphs(body, max_chars):
            chunks.append(Chunk(source, doc_title, title, f"{crumb}\n{piece}"))
    return chunks
