"""Embedding client (Ollama /api/embed)."""
from __future__ import annotations

from typing import Protocol

import requests


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbedder:
    def __init__(self, url: str, model: str, session=None, timeout: int = 600):
        self.url = url.rstrip("/")
        self.model = model
        self.session = session or requests.Session()
        self.timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            resp = self.session.post(f"{self.url}/api/embed", json={"model": self.model, "input": texts}, timeout=self.timeout)
            if resp.status_code >= 400:
                raise RuntimeError(f"embedding failed at {self.url} with model {self.model}: HTTP {resp.status_code} {resp.text[:200]}")
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise RuntimeError(f"embedding failed at {self.url} with model {self.model}: {exc}") from exc
        if "embeddings" not in data:
            raise RuntimeError(f"embedding failed at {self.url} with model {self.model}: no 'embeddings' in response")
        return data["embeddings"]
