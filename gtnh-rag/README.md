# gtnh-rag

A local hybrid-search / retrieval-augmented Q&A tool for GregTech: New Horizons (GTNH).
It builds a searchable knowledge base from the GTNH wiki, the in-game questbook, and full
recipe dumps produced by two client-side mods, then exposes it as a CLI with four
commands: `search` (raw chunk retrieval), `ask` (chat-model answer with citations),
`recipe` (exact recipe/usage lookup by item name), plus `fetch`/`build` to (re)generate
everything from raw sources.

Corpus was rebuilt against pack **2.9.0-RC-2** on 2026-10-05 (first built against
2.9.0-beta-2). Recipe/item ids embedded in the nesql and
NERD dumps are specific to the game session that produced them (ids change across pack
versions) — a dump from a different pack version will not decode correctly against
another pack's world.

## How it works

- **Retrieval** (`gtnh_rag/search.py`): FTS5 BM25 full-text search plus cosine similarity
  over cached embeddings, fused with reciprocal rank fusion (RRF). Works with FTS alone if
  embeddings are missing or incomplete (a warning is printed to stderr).
- **Index** (`gtnh_rag/index.py`): a single SQLite file (`data/index.sqlite`) holding
  `chunks(id, source, doc_title, section, text, sha256)`, an FTS5 virtual table, and an
  `embeddings(chunk_id, model, vector)` table keyed by `(sha256, model)` — unchanged chunks
  are never re-embedded, so `build` is safely resumable and incremental.
- **Recipes** (`gtnh_rag/nesql.py`, `gtnh_rag/recipes.py`): the nesql-exporter HSQLDB script
  is imported almost verbatim into a second SQLite file (`data/recipes.sqlite`), giving an
  exact-lookup tool (`gtnh-rag recipe`) independent of the RAG index — no model involved,
  every recipe producing or consuming an item with its machine, tier, EU/t and duration.
- **Corpus sources** (`gtnh_rag/corpus.py`, `gtnh_rag/nerd.py`): wiki pages (chunked per
  section heading), quests (one chunk per quest: name, quest line, prerequisites, tasks,
  rewards — read from the nesql `QUEST`/`QUEST_LINE`/`TASK`/`REWARD` tables), recipes (one
  chunk per recipe-type/output-item group), and NEI/NERD recipes (a secondary source for
  handlers the nesql dump does not cover, e.g. bee breeding, Chisel, Meteor Ritual).
  Optionally, local markdown notes (e.g. an Obsidian vault) can be mixed in as a fifth
  source — see `vault_dir` below.

## Prerequisites

- Python 3.11+ (uses stdlib `tomllib` for config parsing).
- [Ollama](https://ollama.com) reachable at `embed_url`, with the `bge-m3` embedding model
  pulled (`ollama pull bge-m3`). Any Ollama-compatible embedding model works; `bge-m3` is
  multilingual and was used to build and test the corpus.
- Optionally, an OpenRouter API key (`OPENROUTER_API_KEY`) for `ask` (default model
  `google/gemma-4-31b-it`, pinned to one provider via `chat_provider` so OpenRouter never
  load-balances a cached conversation onto a different provider). Bring your own key:
  https://openrouter.ai/settings/keys
- Two client-side mod dumps to populate `data/raw/nesql/` and `data/raw/nerd/` (see
  below). Neither mod has a release compatible with GTNH 2.9, so both must be built from
  source — see [`../docs/dump-mods.md`](../docs/dump-mods.md).
  `search` and `recipe` work with wiki-only data if you skip these, but recipe lookup and
  the `recipe`/`quest`/`nei` corpus sources need them.

## Install

```
python3 -m venv .venv && .venv/bin/pip install -e . pytest
```

Copy `gtnh_rag.example.toml` to `gtnh_rag.toml` (gitignored — this file holds your local
paths/hosts, never commit it) and adjust it for your machine; every key has a sane default
if you omit it, see [Configuration](#configuration).

## Producing the recipe dumps

Recipe, quest and NEI data are **not** downloaded automatically — they are captured in a
running GTNH client and dropped into `data/raw/`. Neither dump mod has a release
compatible with GTNH 2.9, so both have to be built from source first; the full build and
capture procedure lives in [`../docs/dump-mods.md`](../docs/dump-mods.md).

In short: **nesql-exporter** dumps the full recipe/item/quest database via the `/nesql`
chat command (~1 hour) into `nesql-db.script`/`nesql-db.properties`, and
**NotEnoughRecipeDumps** dumps NEI recipes/recipe stacks not covered by nesql via NEI →
Tools → Dumps (~5 minutes) into `recipes.json`/`recipes_stacks.json`. Copy those files
into `data/raw/nesql/` and `data/raw/nerd/` respectively.

Both dumps must come from the same pack version and, ideally, the same session — recipe
and item ids are internal database ids assigned at dump time, not stable across dumps.

## Usage

```
.venv/bin/gtnh-rag fetch                    # wiki (+ optional vault notes) into data/raw
.venv/bin/gtnh-rag build                    # recipes.sqlite + index.sqlite (+ embeddings)
.venv/bin/gtnh-rag search "LFTR sparging" -k 5
.venv/bin/gtnh-rag recipe "Thorium Dust" --uses
.venv/bin/gtnh-rag ask "how does the LFTR setup work?"
```

`fetch --source wiki|vault|all` (default `all`) downloads the wiki via the MediaWiki API
and, if `vault_dir` is configured, copies local notes; `--source vault` with no
`vault_dir` set is a no-op that exits non-zero.

`build --skip-embed` populates `recipes.sqlite` and `index.sqlite` without calling the
embedder — useful to check chunk counts before committing to the (slow) embedding pass.
`--skip-nesql`/`--skip-nerd` skip re-importing those sources on rebuild. `--force-prune`
allows pruning more than half of an existing source's chunks (a safety check refuses that
by default, in case raw input went missing by accident).

`search --json` is the machine-readable interface: raw chunks, no model, for use as an
agent tool. `ask` answers in Portuguese (pt-BR) by default — see `SYSTEM_PROMPT` in
`gtnh_rag/ask.py` if you want another language; every claim is cited `[n]` against the
returned chunk sources.

## Configuration

All settings live in `gtnh_rag.toml` in the working directory (path overridable with
`--config`); see `gtnh_rag.example.toml` for the full set with comments. Missing keys fall
back to the `Config` defaults in `gtnh_rag/config.py`:

| Key | Default | Notes |
|---|---|---|
| `chat_url` | `https://openrouter.ai/api/v1/chat/completions` | chat endpoint (OpenAI-compatible) |
| `chat_model` | `google/gemma-4-31b-it` | used by `ask` |
| `chat_provider` | `["parasail/fp8"]` | OpenRouter provider pin for `ask`; keep it a single entry so requests never get load-balanced onto a different (cache-missing) provider |
| `embed_url` | `http://localhost:11434` | embedding endpoint (may differ from `chat_url`'s host, e.g. a beefier GPU host) |
| `embed_model` | `bge-m3` | used by `search`/`ask`/`build` |
| `data_dir` | `data` | relative to cwd; also overridable with the `GTNH_RAG_DATA_DIR` env var |
| `wiki_api` | `https://wiki.gtnewhorizons.com/w/api.php` | MediaWiki API |
| `vault_dir` | unset | optional local markdown notes directory mixed in as the `vault` source; `fetch --source vault` is skipped when unset |
| `vps_questbook` | unset | reserved scp-style fallback (`user@host:/path/to/World/betterquesting/QuestDatabase.json`); unused today, quests come from the nesql dump |
| `nerd_handlers` | `[]` | NEI handler class names to pull from the NERD dump (see the example file for the list used to build the corpus) |

## Embeddings

Embedding is resumable: `build` only embeds chunks missing from the `embeddings` table for
the configured model, so interrupting and re-running continues where it left off, and
unchanged chunks keep their cached vector across rebuilds.

Rough sizing from building the corpus once against the real GTNH wiki + questbook + a full
nesql/NERD recipe dump: raw inputs (`data/raw/`) are about 1 GB, each of `recipes.sqlite`
and `index.sqlite` lands around 500 MB, and the corpus is on the order of 90–100k chunks
total. Embedding the full corpus with `bge-m3` on CPU took on the order of tens of hours;
a consumer GPU (e.g. a 6 GB laptop card) brought that down to a few hours. Point `embed_url`
at any Ollama host running `bge-m3` — local CPU, a local GPU, or a remote machine with
`OLLAMA_HOST` set — to trade wall-clock time for hardware.

## Refresh

After a pack update: regenerate the nesql and NERD dumps in the client, copy them into
`data/raw/`, then re-run `fetch` and `build`. Unchanged chunks keep their embeddings, so
only the delta gets re-embedded.

## Tests

```
.venv/bin/python -m pytest -q
```

Tests never touch `data/` or the network — they use small fixtures under `tests/fixtures/`
and a fake embedder.
