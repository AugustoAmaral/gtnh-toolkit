"""Runtime configuration loaded from gtnh_rag.toml (see gtnh_rag.example.toml)."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class Config:
    chat_url: str = "https://openrouter.ai/api/v1/chat/completions"
    chat_model: str = "google/gemma-4-31b-it"
    chat_provider: list[str] = field(default_factory=lambda: ["parasail/fp8"])
    embed_url: str = "http://localhost:11434"
    embed_model: str = "bge-m3"
    data_dir: Path = Path("data")
    wiki_api: str = "https://wiki.gtnewhorizons.com/w/api.php"
    # Optional local markdown notes (e.g. an Obsidian vault) mixed into the corpus as the
    # "vault" source. Unset by default; `fetch --source vault` is a no-op until this is set.
    vault_dir: Path | None = None
    # Optional scp-style fallback for the questbook (e.g. "user@host:/path/to/World/betterquesting/QuestDatabase.json").
    # Unused by v1: quests are read from the nesql dump; kept for a future fallback path.
    vps_questbook: str = ""
    nerd_handlers: list[str] = field(default_factory=list)

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def index_path(self) -> Path:
        return self.data_dir / "index.sqlite"

    @property
    def recipes_path(self) -> Path:
        return self.data_dir / "recipes.sqlite"


_PATH_FIELDS = {"data_dir"}


def load_config(path: Path | None = None) -> Config:
    """Load gtnh_rag.toml (cwd by default). Missing file or keys fall back to defaults.

    ``GTNH_RAG_DATA_DIR``, if set, overrides ``data_dir`` regardless of the config file.
    """
    path = Path(path) if path else Path("gtnh_rag.toml")
    values: dict = {}
    if path.exists():
        with path.open("rb") as fh:
            try:
                values = tomllib.load(fh)
            except tomllib.TOMLDecodeError as exc:
                raise RuntimeError(f"invalid config {path}: {exc}") from exc
    known = {f.name for f in fields(Config)}
    kwargs = {}
    for key, value in values.items():
        if key not in known:
            continue
        if key in _PATH_FIELDS:
            kwargs[key] = Path(value)
        elif key == "vault_dir":
            kwargs[key] = Path(value) if value else None
        else:
            kwargs[key] = value
    env_data_dir = os.environ.get("GTNH_RAG_DATA_DIR")
    if env_data_dir:
        kwargs["data_dir"] = Path(env_data_dir)
    return Config(**kwargs)
