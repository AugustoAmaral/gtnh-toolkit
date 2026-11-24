# Roadmap

Work planned for the toolkit, roughly in priority order. Each item comes from a real gap hit
while running the server; the directory READMEs describe the current behaviour.

## Safety and tests

- **Tests for `ae2-tools/`.** It is the only tool that writes into a save, and today it has no
  tests. Cover the NBT read/write round trip, every `review` verdict, and `--dry-run` producing
  no writes, using small hand-made region fixtures.
- **Tests for `planners/` and `perf-tools/`.** Small fixture databases and collapsed profiles,
  so refactors below don't change results silently.
- **CI.** A GitHub Actions workflow running every test suite (`gtnh-rag/` already has one) on
  each push and pull request.

## `ae2-tools/`

- **Catch recipes that changed across pack updates.** `MATCH_CIRCUIT_DIFF` assumes a missing
  circuit or fluid is harmless; if a pack update *added* one, the pattern is broken and the
  verdict hides it. Compare against the previous corpus when one is available and flag those
  patterns separately.

## `perf-tools/`

- **One CLI instead of loose scripts.** Fold `an2.py`, `tree.py`, `callers.py`, `diff.py`,
  `ents.py`, `forced2.py` and `chunk.py` into a single entry point with subcommands, shared
  argument parsing and `--help`. `chunk.py` takes its targets from the command line instead
  of a list edited at the top of the file.

## `planners/`

- **A generic production-chain planner.** Generalize the LFTR Fuel 3 calculator: given a
  target output rate, walk `recipes.sqlite`, pick recipes, and report machines, external
  inputs and surplus for any chain, not only the fuel loop.

## `gtnh-rag/` and data

- **Corpus refresh for the next pack release.** Rebuild the dumps and the index against the
  next GTNH 2.9 release and update the version labels.
- **Scripted dump-mod build.** Turn the manual walkthrough in
  [`docs/dump-mods.md`](docs/dump-mods.md) into a script that checks out the two mods at the
  pinned commits, applies the template migration and builds the jars, so the next pack
  version is a one-command rebuild.
