# Building the dump mods and capturing the recipe dumps

`gtnh-rag` needs three raw inputs before `fetch`/`build` can produce a corpus: the GTNH
wiki (fetched automatically, no action needed), and two dumps captured from a running
GTNH client — a full recipe/item/quest database (**nesql-exporter**) and a secondary
NEI recipe dump (**NotEnoughRecipeDumps**, "NERD") that covers handlers nesql does not.

This repo ships **no data and no patched jars**. Both dump mods have to be built from
source before you can produce those two dumps. This document is the missing step the
main READMEs point at.

## Why build from source at all

Neither mod has a released build compatible with GTNH 2.9:

- **nesql-exporter** needs one dependency-version bump to compile against a current
  Gradle toolchain.
- **NotEnoughRecipeDumps** was last released against an old GTNH mod template, pinned to
  NEI/GT5-Unofficial versions well behind a 2.9 pack's `mods/` folder; loading that
  release jar on a current instance fails at runtime.

There is no way around building both from source. The facts, versions and numbers below
come from one real run against pack **2.9.0-beta-2** (world state on
**2.9.0-beta-3**), done on 2026-09-06. Gradle provisions its own JDKs (Zulu 8/17/21) for
both builds — you do not need a system `java` install to build them, only whatever your
OS needs to run Gradle itself.

## What you need before starting

- A GTNH **2.9** client instance (any launcher) with `mods/` already populated for the
  pack version you intend to dump — the two mods you build here get *added* to that
  `mods/` folder, they do not replace it.
- The exact `NotEnoughItems` and `GT5-Unofficial` jar versions shipped in that instance's
  `mods/` (read the filenames — you need them for the NERD patch below).
- A JDK-capable build machine for each mod (it can be the same machine as the client, or
  not — you only need the built jars on the client machine). Internet access for Gradle
  to download dependencies and its own JDKs.
- Git, to clone the two source repos.
- Disk space: several hundred MB for the two build trees plus Gradle caches, and **at
  least ~1 GB free** on the client machine for the dump output itself (see sizes below).
- Patience: the nesql dump alone takes about an hour of wall-clock time with the game
  effectively paused.

## 1. nesql-exporter (recipe/item/quest database)

- Source: fork [`ShadowTheAge/nesql-exporter`](https://github.com/ShadowTheAge/nesql-exporter),
  commit `b5b896e`.
- Patch: in `build.gradle.kts`, bump the `retrofuturagradle` plugin version from `1.3.35`
  to `1.4.9`. The pinned `1.3.35` fails against a current Gradle; `1.4.9` builds cleanly.
  No other source change is needed.
- Build:

  ```sh
  git clone https://github.com/ShadowTheAge/nesql-exporter.git
  cd nesql-exporter
  git checkout b5b896e
  # apply the retrofuturagradle bump described above, then:
  ./gradlew build
  ```

- Output (in `build/libs/`): **two** jars, and both must go into the client's `mods/`:
  - the mod jar itself, `NESQL-Exporter-0.5.7-*.jar` (~420 KB);
  - its dependency jar, `*-deps.jar` (~24 MB — bundles Hibernate and HSQLDB, which the
    exporter needs at runtime and which are not otherwise present in a GTNH instance).

## 2. NotEnoughRecipeDumps (NERD, secondary NEI recipe source)

- Source: [`Hermanoid/NotEnoughRecipeDumps`](https://github.com/Hermanoid/NotEnoughRecipeDumps),
  `master` at commit `5535135` (2025-07-15).
- Why the released build doesn't work: upstream's buildscript still uses the old GTNH
  mod template, and `dependencies.gradle` pins `NotEnoughItems 2.6.52-GTNH` and
  `GT5-Unofficial 5.09.50.111` — both stale relative to a current 2.9 pack.
- Patch (migrate to the current GTNH template):
  1. Replace `build.gradle.kts` and `settings.gradle.kts` with the current GTNH
     `gtnhconvention` convention-plugin setup (the one current GTNH mod templates use),
     and take `gradle.properties` from the current GTNH mod template.
  2. In `dependencies.gradle`, bump `NotEnoughItems` and `GT5-Unofficial` to the versions
     the *target instance's* `mods/` actually ships (in the reference run: `NotEnoughItems
     2.8.111-GTNH` and `GT5-Unofficial 5.09.54.20` — treat these as an example, not a
     pin; match your own instance).
  3. One source consequence of the template migration: the generated `Tags` class no
     longer exposes `MODID`/`MODNAME`. Replace those references with the literal strings
     `notenoughrecipedumps` and `NotEnoughRecipeDumps` in the `@Mod` annotation in
     `NotEnoughRecipeDumps.java` and in the log line in `CommonProxy.java`.
  - No dump logic is touched by this patch — only the build wiring and those two literal
    strings.
- Build:

  ```sh
  git clone https://github.com/Hermanoid/NotEnoughRecipeDumps.git
  cd NotEnoughRecipeDumps
  git checkout 5535135
  # apply the template migration and dependency bump described above, then:
  ./gradlew build
  ```

- Output: one jar in `build/libs/` (~27 KB) — copy it to the client's `mods/`.

## 3. Capturing the dumps

Both dumps must come from **the same pack version and, ideally, the same client
session** — the ids inside each dump (item ids, recipe ids, quest ids) are internal
database ids assigned at dump time, not stable across dumps or pack versions. A nesql
dump and a NERD dump taken on different pack versions (or even two different sessions on
the same version) do not necessarily agree on what a given id means.

1. Copy the three jars into the instance's `mods/`: the nesql mod jar, the nesql
   `-deps.jar`, and the NERD jar.
2. If the instance has a `bugtorch-*.jar`, **remove it before dumping** — nesql-exporter's
   own README reports a conflict with it in the enchanted-item rendering path used during
   the export. Put it back afterward if you normally run with it.
3. Launch the client and enter a **brand-new creative world** — the exporter reads
   player state, so a fresh world avoids surprises from an existing save. Open the
   inventory once and **let the NEI item list finish loading** before dumping anything;
   dumping too early silently omits items that NEI hasn't indexed yet.
4. Run nesql: type `/nesql` in chat. It takes about an hour; the game may appear paused
   while it walks every recipe and item. Output lands in the client's `nesql/` directory
   (e.g. `.minecraft/nesql/`): `nesql-db.script` (~420 MB) and `nesql-db.properties`.
   Forge will log a complaint about `System.exit()` when the export finishes — that is
   expected, see [Troubleshooting](#troubleshooting).
5. Run NERD: NEI → Tools → Dumps → **Recipes**, then also **Recipe Stacks**. Takes about
   5 minutes. Output lands in the instance's `dumps/` directory: `recipes.json`
   (~540 MB) and `recipes_stacks.json` (~11 MB).

## 4. Wiring the dumps into gtnh-rag

1. Copy `nesql-db.script` and `nesql-db.properties` into `data/raw/nesql/` (inside your
   `gtnh-rag` working copy).
2. Copy `recipes.json` and `recipes_stacks.json` into `data/raw/nerd/`.
3. Update `data/raw/manifest.json` with the source pack version and the dump date (see
   `gtnh_rag.example.toml`/the `gtnh-rag` README for the manifest's role — every consumer
   of the recipe database, including `ae2-tools`, reads the pack label from here).
4. From the `gtnh-rag` working copy:

   ```sh
   .venv/bin/gtnh-rag fetch
   .venv/bin/gtnh-rag build
   ```

No separate step is needed for the questbook: quests come from the nesql dump's
`QUEST`/`QUEST_LINE`/`TASK`/`REWARD` tables, and `build` reads them from
`data/raw/nesql/` directly. The wiki is downloaded automatically by `fetch`.

## Troubleshooting

- **`NoSuchMethodError`/`NoClassDefFoundError` mentioning `codechicken.nei` (or a GT5U
  class) when the client loads the NERD jar.** The jar was built against a different
  `NotEnoughItems`/`GT5-Unofficial` API than the instance is running. Fix: edit
  `dependencies.gradle` to match the exact `NotEnoughItems`/`GT5-Unofficial` versions in
  the target instance's `mods/`, then rebuild with `./gradlew build` — the new jar lands
  in `build/libs/`. This is an API-compatibility problem, not a logic bug; there is no
  patch that avoids it other than tracking the instance's actual mod versions.
- **Forge logs a `System.exit()`-related complaint right after `/nesql` finishes.** This
  is expected — nesql-exporter calls `System.exit()` to terminate its export cleanly, and
  Forge notices and logs it. It does not affect the dump; `nesql-db.script` is already
  written by the time it appears. Ignore it.
- **Dump looks smaller than expected, or items are missing.** The known cause is dumping
  before the NEI item list finished loading in a fresh world: NEI hasn't indexed
  everything yet and the missing items are simply absent, with no error. Redo the capture
  with the item list fully loaded (and with `bugtorch` removed, per step 2).
- **`ae2-tools` or `gtnh-rag recipe` reports items/recipes it doesn't recognize against
  your save.** That is very likely a pack-version mismatch between the dump and the
  world, not a build problem — see the "Recipe and item ids are per pack version"
  warning in the root README and the id-gotcha section of `ae2-tools/README.md`.
