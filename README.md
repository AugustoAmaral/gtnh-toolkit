# gtnh-toolkit

Tooling for running, diagnosing and repairing a **GregTech: New Horizons 2.9** server
(Minecraft 1.7.10, Forge + lwjgl3ify under Java 21) and the bases inside it.

Everything here was written to solve a concrete problem on a private server and then
generalized. Nothing calls a paid API, nothing phones home, and every tool runs offline
except the knowledge-base fetcher (which talks to the public GTNH wiki) and the optional
chat-answer command (which talks to your own Ollama).

| Directory | What it gives you |
|---|---|
| [`gtnh-rag/`](gtnh-rag/) | A local knowledge base and Q&A CLI over the GTNH wiki, the questbook and full recipe dumps (~250k recipes with machine, tier, EU/t and duration). Hybrid BM25 + embedding search; exact recipe lookup needs no model at all. |
| [`ae2-tools/`](ae2-tools/) | Reads every AE2/AE2FC Encoded Pattern and ME Interface out of a save, judges each pattern against the recipe database deterministically, and can write corrections back into the region files. Built after a pack update silently broke ~180 patterns. |
| [`perf-tools/`](perf-tools/) | async-profiler and region-file analysis scripts for "why does this server burn CPU with nobody online", plus the CPU-per-tick measurement that `/forge tps` gets wrong. |
| [`planners/`](planners/) | Production-chain calculators that read the real recipes instead of hand-typed numbers. Currently the closed-loop LFTR Fuel 3 balance. |

Each directory has its own README with prerequisites, usage and caveats. Read the one you
need — they are self-contained.

Planned work is listed in [`ROADMAP.md`](ROADMAP.md).

## Where to start

- **"What's the recipe for X / how does Y work?"** → `gtnh-rag/`. Heaviest setup (two
  client-side mod dumps plus an embedding pass), highest payoff. The `recipe` command works
  as soon as the recipe database is built, with no model involved.
- **"Autocrafting broke after the pack update."** → `ae2-tools/`, start with
  `python3 ae2.py audit`. It needs a `recipes.sqlite` from `gtnh-rag/`.
- **"The server is lagging and `/forge tps` says it's fine."** → `perf-tools/`, start with
  `cpu_per_tick.sh`.
- **"How many machines do I need for N reactors?"** → `planners/`.

## Requirements

Python 3.11+ (3.11 for `tomllib`) and standard library only, except:

- `nbtlib==2.0.4` for anything that reads NBT (save files) — `ae2-tools/`, two of the
  `perf-tools/` scripts.
- `gtnh-rag/` additionally needs an Ollama instance for embeddings (`bge-m3`) and, if you
  want prose answers, a chat model. A GPU makes the embedding pass hours instead of days.

## Warnings worth repeating

- **The tools in `ae2-tools/` can write into your save.** Stop the server, back up the
  region files, use `--dry-run` first. GTNH saves are not trivially repairable.
- **Recipe and item ids are per pack version.** A recipe dump taken on one pack version does
  not decode a world from another — `ae2-tools/` has explicit verdicts for that situation, and
  ignoring them produces confident nonsense. Both READMEs explain the trap.
- **No data is included.** Wiki text, recipe dumps, profiles and save dumps are yours to
  generate; the repo ships code and documentation only.

## Rebuilding the data

No data ships with this repo. The GTNH wiki is fetched automatically by `gtnh-rag
fetch`. The recipe dumps that back `gtnh-rag recipe` and `ae2-tools` require building two
client-side mods from source — neither has a release compatible with GTNH 2.9 — a full
walkthrough is in [`docs/dump-mods.md`](docs/dump-mods.md). The NBT-reading tools in
`ae2-tools/` need `nbtlib==2.0.4`; if installing it fails on a managed system Python, see
the offline wheel-extraction fallback in the "Setup" section of
[`ae2-tools/README.md`](ae2-tools/README.md).

Pack version these were built and verified against: **2.9.0-beta-2** corpus, **2.9.0-beta-3**
world. MIT licensed — see [`LICENSE`](LICENSE).
