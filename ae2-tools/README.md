# AE2 Toolkit (GTNH)

> **⚠ These tools read and, on request, write directly into your Minecraft save's region files
> (`.mca`) and `level.dat`/playerdata NBT.** GTNH saves are not trivially repairable — a bad write
> can corrupt a chunk or an inventory beyond casual recovery. **Always stop the server and back up
> the region files before running anything that writes** (`apply-names`, `apply-patterns`). Every
> write path defaults to `--dry-run` and requires an explicit `--backup-dir`; do not skip that
> discipline even when you're confident the plan is correct.

Reusable tools to read, review and fix AE2/AE2FC Encoded Patterns and ME Interfaces in a GTNH
2.9 (Minecraft 1.7.10) save. This directory only has code; keep your data (dumps, plans,
verdicts, audits) in a directory of your own outside version control — never commit save data
here.

**Every script accepts `--help`** and assumes nothing beyond what you pass as a flag (no hardcoded
`/tmp` path). Everything runs on Python 3 + stdlib + `nbtlib==2.0.4` (which in turn pulls in
`numpy`); `audit.py`/`export_patterns.py`/`review_matcher.py`/`build_interfaces.py` also use
`sqlite3` (stdlib) against a `recipes.sqlite` recipe database (see the sibling `gtnh-rag/`
project for how to build one). Only the tools that read NBT (`dump`, `scan`, `idmap`, `apply-*`,
and `audit` when it runs with `--region-dir`) need `nbtlib` — `audit --patterns/--idmaps`,
`export`, `review` and `diff-audits` run on stdlib alone.

## Setup (local `nbtlib`, if your Python doesn't have it)

The system Python often lacks `nbtlib`. If `pip install --user nbtlib==2.0.4` fails with
`externally-managed-environment`, extract the wheel manually (works without a venv on recent
Python):

```sh
mkdir -p ./pylib
curl -sL https://files.pythonhosted.org/packages/py3/n/nbtlib/nbtlib-2.0.4-py3-none-any.whl -o nbtlib.whl
python3 -m zipfile -e nbtlib.whl pylib/
python3 -m pip download numpy --dest wheels --only-binary=:all: --no-deps
python3 -m zipfile -e wheels/numpy-*.whl pylib/
export PYTHONPATH="$(pwd)/pylib"   # before running any script below
```

On a typical server host (Ubuntu, recent Python) `nbtlib` may already be installed — check with
`python3 -c "import nbtlib; print(nbtlib.__version__)"` before installing again.

## Single entry point (`ae2.py`)

`python3 ae2.py <subcommand> --help` is identical to running the corresponding script directly —
the wrapper only saves you from having to remember eleven file names. **Start with `audit`**: it
does the entire pipeline by itself and is the only command you need to answer "what's broken
today?".

| subcommand | script | what it does |
|---|---|---|
| `audit` | `audit.py` | **one command**: scan → string identities → verdict → coverage statement |
| `diff-audits` | `diff_audits.py` | diff two audits, matched by identity `(te,x,y,z,path)` |
| `export` | `export_patterns.py` | every pattern as a string identity (JSON + one line per pattern) |
| `dump` | `dump_tes.py` | dump every TileEntity of a `region/` dir to JSON.gz |
| `scan` | `scan_patterns.py` | scan for Encoded Patterns across dimensions (+ playerdata) |
| `idmap` | `idmaps.py` | diff FML numeric ids between `level.dat` snapshots |
| `interfaces` | `build_interfaces.py` | inventory + proposed name for every ME Interface |
| `review` | `review_matcher.py` | deterministic verdict for every pattern vs. `recipes.sqlite` |
| `gemma-review` | `gemma_review.py` | **optional, outside of `audit`**: LLM second opinion on the residue (measured unreliable, see below) |
| `apply-names` | `apply_interface_names.py` | writes interface names into the save (server stopped) |
| `apply-patterns` | `apply_pattern_fix.py` | writes pattern fixes into the save (server stopped) |

No step of the audit path (`audit`, `export`, `review`, `diff-audits`) calls an LLM: it is fully
deterministic, same input → same output.

## Reading vs. writing: what's safe with the server running

**Reading can be done with the server running** (`dump_tes.py`/`scan_patterns.py`/`idmaps.py` only
read bytes from `.mca`/`.dat` files; a chunk being saved mid-read is simply counted as `bad`, it
never blocks or corrupts anything). **Any write (`apply-names`, `apply-patterns`) requires the
server stopped** — never run against a save with players online. Re-reading a chunk while the
server process is rewriting it corrupts the save.

The two write tools share the same discipline:
- Default mode is `--dry-run`: prints every planned change, writes nothing.
- `--apply` requires an explicit `--backup-dir`; it copies each region/playerdata file there
  *before* rewriting it, and refuses to overwrite an existing backup.
- `--verify` re-reads the files afterward and confirms every planned change actually landed,
  independent of the write path that made it.
- Both tools validate the *entire* plan against the live NBT before writing anything — if any
  single change would fail, nothing is written.

## Data formats

- **Patterns** (`scan_patterns.py` output / the `in`/`out` fields of `build_interfaces.py`): each
  slot is `null` (empty) or `{id, dmg, n, tag, extra?}`. `id`/`dmg` are the numeric ids **of that
  world** (see the id gotcha below). `n` is the NBT `Count` (a byte, max 127). **Prefer
  `extra.Cnt` over `n` when present**: this AE2 fork stores counts above 127 (and AE2FC fluid mB
  amounts) in a `Cnt` tag (long) while leaving `Count` at `0` or `1` — reading only `Count` loses
  the real quantity of a large share of patterns in a typical save (mostly Dual Interfaces and
  cable parts). `tag` is the item's full NBT (mold, circuit, etc.) when present.
- **Identity of a pattern**: `(te, x, y, z, path)`. `path` is the NBT path generated by the
  scanner's `walk()`: `/key` descends a Compound, `[N]` (chained, no leading slash) indexes a List
  — e.g. `/patterns/#22` (block interface), `/extra:5/patterns/#3` (cable part, side 5),
  `/Inventory[4]` (player inventory). **File/scan indices are NOT identity** — the total pattern
  count changes between scans whenever a player moves patterns in or out of the ME network; always
  re-resolve by `(te,x,y,z,path)` against a fresh scan before acting on a plan.
- **Name of an interface**: `customName` (String) at the TE root, for `BlockInterface` (block ME
  Interface) and `fluid_interface` (block Dual Interface, AE2FC) — both inherit from
  `appeng.tile.AEBaseTile` (confirmed by decompiling `readFromNBT`/`writeToNBT` and by cross-
  checking against already-named interfaces in a live save). For an interface that is a **cable
  part** (`BlockCableBus`), the name is the *display name of the part's ItemStack*:
  `def:<side>/tag/display/Name` — `extra:<side>` (where the part's patterns/config live) has no
  name key. Sides: 0=down 1=up 2=north 3=south 4=west 5=east.
- **String identity of an item/fluid** — what `export`/`review`/`audit`/`diff-audits` compare:
  item = `"<modid>:<internal_name>/<damage>"` (e.g. `gregtech:gt.metaitem.01/32708`), fluid =
  `"fluid:<internal_name>"` (e.g. `fluid:molten.tungsten`). An item with durability
  (`ITEM.MAX_DAMAGE > 0`) compares while ignoring damage and appears as `.../*` (e.g.
  `IC2:itemBatLamaCrystal/*`). **Never compare by the world's numeric id** — it only survives as
  the informational `world_id` field of each stack.
- **Export** (`export_patterns.py`, or an audit's `patterns_named.json`): a list 1:1 with scan
  order, each entry `{index, te, x, y, z, path, where, crafting, substitute, in: [...], out: [...],
  text}`. Each `in`/`out` slot is `null` (empty) or:
  - item: `{kind:"item", key:"modid:name/dmg", reg, dmg, name, n, nbt, world_id}` — `name` is the
    localized name (`null` if the corpus doesn't know the item, or when run without `--db`), `n`
    is the **real** count (`Cnt` when present, else `Count`), `nbt` is the stack's `tag`.
  - fluid: `{kind:"fluid", via:"fluid_drop"|"raw", key:"fluid:<internal>", fluid, name, mb}` —
    `fluid_drop` is the `ae2fc:fluid_drop` item with `tag.Fluid`; `raw` is a bare `FluidStack`
    entry in the pattern's list (no `id`), as AE2FC writes in some patterns.
  - removed: `{kind:"removed", world_id, dmg, n}` — a numeric id that no longer exists in the
    world's registry, i.e. a genuinely broken pattern (the item left the pack).
  - `text` is the human-readable line for the pattern:
    `"128x Indium Gallium Phosphide Dust + 9216mB Molten Naquadah -> 16x UHPIC Wafer"` — falls back
    to the string key when there's no localized name, and a programmed circuit shows as
    `circuit 3`.
  - `patterns_named.txt` has one line
    `"#<index> <te> (<x>,<y>,<z>) <path> [crafting|processing]: <text>"` per pattern: the file to
    read "my world's recipes" and to diff by eye.

## Verdicts (`review` / `audit`)

| verdict | meaning | what to do |
|---|---|---|
| `MATCH` | inputs and outputs match a recipe of the corpus (including an integer multiple `k`) | nothing |
| `MATCH_CIRCUIT_DIFF` | matches except for a **minor difference**: a programmed circuit, an unconsumed item that stays in the machine (mold/shape/lens), or a fluid the pattern doesn't carry (comes in via hatch/pipe) | nothing, but it's an assumption: if the recipe *gained* a circuit/fluid in a pack update, the pattern is actually broken and this verdict won't catch it |
| `MISMATCH` | a recipe exists for the output, but the inputs diverge | check: the best candidate, the string-key diff (`diff_keys`), and both sides' `text` are in the JSON and the report |
| `NO_RECIPE_FOUND` | every item of the pattern exists in the corpus, but **no** recipe of it produces that output | check in-game (NEI): either the recipe left the pack, it's a two-step pattern, or the output is wrong |
| `CORPUS_UNKNOWN_ITEM` | an item/fluid of the pattern exists in the world but **not** in the corpus | the corpus has no basis to judge it — this is the direct measure of the gap between the dump's pack version and the running one |
| `ITEM_REMOVED` | a numeric id that no longer exists in the world's registry | genuinely broken: delete or re-encode |

`summary.txt`/`review.md` open with the **coverage statement**: how many patterns the corpus could
judge (`MATCH` + `MATCH_CIRCUIT_DIFF` + `MISMATCH` + `NO_RECIPE_FOUND`) versus how many it couldn't
(`CORPUS_UNKNOWN_ITEM` + `ITEM_REMOVED`), with the corpus's version on the first lines. This is
what lets you say "nothing else is broken" without bluffing: the claim only holds for the block
that could be judged.

Every `MATCH`/`MATCH_CIRCUIT_DIFF` also carries `via_oredict`: for each slot whose recipe accepts
more than one item (an OreDict group), which specific item the pattern uses
(`{slot_alternatives, used, used_name}`). Without this, an `any:stick` matched by one specific
stick would be hidden inside a silent `MATCH`.

## The numeric-id gotcha (read before resolving any `id`)

Minecraft 1.7.10/Forge assigns numeric item/block ids **per world**, the first time each registry
entry loads. The same number (`item:4358`, say) can be a different item:
- in a client/NEI dump (a different world, a different mod load order),
- in an older `level.dat` (from before a pack update),
- and in the save you're reading right now.

**Never assume an id is stable.** Every script that resolves id→name uses `idmaps.py`, which reads
`FML.ItemData` from the exact `level.dat` the ids came from — the NEI dump behind a
`recipes.sqlite` typically has its **own** id map, which does not decode patterns from a save built
against a different pack build. The bridge between the two worlds is the **registry name**
(`modid:internal_name`) and, for GT machine blocks, GregTech's own `mID` (which *is* stable across
pack versions — GT uses a fixed metadata value per machine; build a `{gt_mID: "Machine Name"}`
lookup once from GT's source/NEI and reuse it) — never the Forge numeric id. `idmaps.py --out
idmaps.json` writes a `"live"` key = the world's registry **right now**; it's the only map the
other scripts should treat as ground truth for decoding a dump made against that same world.

## `recipes.sqlite` schema cheat sheet

Read-only, and there's normally no `sqlite3` CLI on the machine — always access it via Python
(`sqlite3.connect(f"file:{path}?mode=ro", uri=True)`) or the sibling `gtnh-rag` CLI
(`gtnh-rag recipe "<item>" [--uses]`, already built for this). Main tables:

- `ITEM(ID, MOD_ID, INTERNAL_NAME, ITEM_DAMAGE, LOCALIZED_NAME, NBT)` — `ID` has the shape
  `i~<modid>~<internal_name>~<damage>` (e.g. `i~gregtech~gt.metaitem.01~32708`); it's the key that
  links a `(modid:internal_name, damage)` resolved via `idmaps.json` to a recipe.
- `FLUID(ID, INTERNAL_NAME, LOCALIZED_NAME)` — AE2FC fluids (`ae2fc:fluid_drop`/
  `ae2fc:fluid_packet`) become this via the `Fluid`/`FluidStack.FluidName` NBT field.
- `RECIPE(ID, RECIPE_TYPE_ID)`, `RECIPE_TYPE(ID, TYPE)` — `TYPE` looks like
  `"Circuit Assembler (LV)"`; strip the `" (TIER)"` suffix to group by "recipe map" (Circuit
  Assembler at any tier). Multi-map machines (Material Press = Bending+Forming Press, Amazon Chest
  = Packager+Unpackager, Magnetic Flux Exhibitor = Polarizer+Electromagnetic Separator, Dangote =
  Distillation Tower+Distillery, Ore Washing Plant = Ore Washer+Chemical Bath) accept recipes from
  any of their maps.
- `RECIPE_ITEM_GROUP` / `ITEM_GROUP_ITEM_STACKS` — OreDict alternatives for an input slot.
- `RECIPE_ITEM_OUTPUTS(RECIPE_ID, ITEM_OUTPUTS_VALUE_ITEM_ID)`, `RECIPE_FLUID_OUTPUTS(RECIPE_ID,
  FLUID_OUTPUTS_VALUE_FLUID_ID)` — join with `RECIPE`/`RECIPE_TYPE` to find "which map(s) produce
  this item/fluid".
- Always flag a **pack-version mismatch** between the recipe dump and the running save when using
  this to validate a pattern: a recipe that changed between pack versions gives a false
  `MISMATCH`/`?` — the matcher and `build_interfaces.py` document this, but whoever reads the
  result needs to remember it too.

## End-to-end workflows

### 0. One-command audit (start here)

```sh
# reads the save directly (server may be running) and writes an audit directory
python3 ae2.py audit --region-dir World/DIM<id>/region --level level_live.dat \
  --db recipes.sqlite --out-dir audits/<date> --label post-fix
cat audits/<date>/summary.txt          # the coverage statement, plain text
less audits/<date>/review.md           # the mismatches, one by one, with location and diff
less audits/<date>/patterns_named.txt  # "my world's recipes", one line per pattern

# and what changed since the previous audit:
python3 ae2.py diff-audits audits/<earlier-date> audits/<date> --out audits/diff.md
```
Without `nbtlib` handy (or with a scan you already made), the same audit runs from prebuilt files:
```sh
python3 ae2.py audit --patterns patterns.json.gz --idmaps idmaps.json.gz \
  --db recipes.sqlite --out-dir audits/<date> --label pre-fix
```
`--out-dir` is created and is self-contained — `patterns.json.gz`, `idmaps.json.gz`,
`patterns_named.json`, `patterns_named.txt`, `review.json`, `review.md`, `summary.txt`,
`meta.json` (with the sha256 of `recipes.sqlite`, the corpus label, timestamps, and the sha256 of
every tool used). `audit` refuses to write into a non-empty directory without `--force`, and
always exits 0 when it runs: it's a report, not a gate. `--players <playerdata>` includes patterns
stored in a player inventory; `--manifest` points at gtnh-rag's `manifest.json` when it isn't at
`<dir of --db>/raw/manifest.json`.

`diff-audits` matches patterns by identity `(te,x,y,z,path)` — **never by scan index**, which
changes whenever the total pattern count changes — and shows: only in A, only in B, same identity
with different content (a re-encoded pattern), and verdict transitions (`MISMATCH -> MATCH`,
counted and listed). Both sides' corpus label appears in the header, so a dump swap (e.g. a pack
update between the two audits) is visible instead of being misread as a change in the world.

### 1. Review every pattern step by step (what `audit` does internally)

```sh
python3 ae2.py dump --region-dir World/DIM<id>/region --out dump_tes.json.gz
python3 ae2.py scan World/DIM<id> --out patterns.json
python3 ae2.py idmap --level live=level_live.dat --out idmaps.json   # + --level of older commits if you want GONE/RENAMED
python3 ae2.py export --patterns patterns.json --idmaps idmaps.json --db recipes.sqlite \
  --out-json patterns_named.json --out-txt patterns_named.txt
python3 ae2.py review --patterns patterns.json --idmaps idmaps.json \
  --out-json review_all.json --out-md review_all.md
```
`review_all.md` opens with the coverage statement and the per-verdict counts before the listings.
`review_all.json` is `{meta, results}`: `meta` carries the corpus label (`meta.corpus`, read from
gtnh-rag's `manifest.json`), the counts, and the coverage; `results` is 1:1 with scan order.

**Optional, and not part of `audit`:**
```sh
python3 ae2.py gemma-review --review review_all.json --md review_all.md
```
Sends the residue (`MISMATCH` + `NO_RECIPE_FOUND`) to a cheap LLM as a second opinion. **Measured
unreliable**: in one evaluation run, with 40 blind control patterns, the model got 40/40 controls
right but 6 of the 9 "SAME" verdicts it gave on the residue were hallucinated leniency (checked
against the DB) — the LLM never decides anything on its own here, and `audit` never calls it.

### 2. Propose names for ME Interfaces

```sh
python3 ae2.py interfaces --dump dump_tes.json.gz --patterns patterns.json --idmaps idmaps.json \
  --mid-names mid_names.json --region-dir World/DIM<id>/region \
  --dim-label DIM<id> \
  --out-json interfaces.json --out-proposal interface_names_proposal.json --out-md interfaces.md
# review interfaces.md; only then:
python3 ae2.py apply-names --proposal interface_names_proposal.json \
  --region-dir World/DIM<id>/region --dry-run     # check the list of changes
python3 ae2.py apply-names --proposal interface_names_proposal.json \
  --region-dir World/DIM<id>/region --apply --backup-dir /path/to/backup --verify
```
`--mid-names` is a `{gt_mID: "Machine Name"}` map you build once from GregTech's own machine-id
data (GT's `mID` is stable across pack versions, unlike Forge's numeric ids — see the id gotcha
above). Without `--region-dir`, EnderIO conduits adjacent to an interface are conservatively left
unnamed (treated as "connected", never assumed "loose") — pass `--region-dir` whenever you can to
reduce the number of `?` results for that reason.

### 3. Fix broken patterns (applying a fix_plan.json)

```sh
python3 ae2.py apply-patterns --plan fix_plan.json --dump-tes dump_tes.json.gz \
  --region-dir World/DIM<id>/region --dry-run
python3 ae2.py apply-patterns --plan fix_plan.json --dump-tes dump_tes.json.gz \
  --region-dir World/DIM<id>/region --apply --backup-dir /path/to/backup --verify
```
`fix_plan.json` is a list of `{id, te, x, y, z, path, action: "delete"|"replace", reason, human,
source, old, new}` — the exact shape is documented by `apply_pattern_fix.py --help`. `--dump-tes`
must be a dump taken **before any fix has been applied** (it's used only to resolve list-indexed
slots by their stable `Slot` tag, not by index, which shifts on every delete).

**No generic tool in this toolkit produces `fix_plan.json` for you** — `review_matcher.py` only
gives a verdict (MATCH/MISMATCH/...); deciding "delete" vs. "re-encode with which recipe" for each
broken pattern requires judgment on top of the verdict (did the recipe move to a different tier?
did the item leave the pack? is this actually one half of a working two-step chain?). In practice
this means writing a small, save-specific planning script that imports `review_matcher.py` as a
library to reclassify candidates, manually buckets the ambiguous cases, and then writes the plan —
there is no way to make that decision layer generic across saves, so it isn't shipped here. When
you build your own: keep patterns that are legitimately part of a multi-step chain untouched by
design (e.g. an EBF→freezer chain where a later step's output is intentionally not itself a
finished recipe), leave alone anything whose output is recomputed by AE2 itself at craft time, and
record a reason per pattern for anything you can't classify automatically.

### 4. Reproducing everything from scratch (new session, no prior context)

```sh
# on the machine that hosts the save (server stopped, or read-only reads while it's running):
python3 dump_tes.py --region-dir World/DIM<id>/region --out dump_tes.json.gz
python3 scan_patterns.py World/DIM<id> --out patterns.json
# idmaps: grab the live level.dat, plus an older commit's level.dat if you want GONE/RENAMED
python3 idmaps.py --level live=level_live.dat --level <rev>=level_<rev>.dat --out idmaps.json
python3 ae2.py audit --patterns patterns.json --idmaps idmaps.json --out-dir audits/<date>   # verdict + coverage + export
python3 ae2.py interfaces --dump dump_tes.json.gz --patterns patterns.json --idmaps idmaps.json \
  --mid-names mid_names.json --region-dir World/DIM<id>/region \
  --out-json interfaces.json --out-proposal proposal.json --out-md interfaces.md
```
Everything above runs **read-only** against the save (the server can be up). Only the two
`apply-*` commands at the end of each workflow write, and only with an explicit `--apply`.

## Known limits

- **Pack-version drift**: a `recipes.sqlite` built from one pack version does not perfectly judge a
  save running a different one — a recipe that was retiered or rebalanced between versions
  produces a false `MISMATCH`/`NO_RECIPE_FOUND`. `meta.corpus`/the coverage statement always
  report which corpus was used so you can factor this in; there is no automatic correction for it.
- **The FML numeric-id trap** (see above) means an `idmaps.json` built from one world's
  `level.dat` must never be used to decode a dump from a different world or a different point in
  time of the same world — always regenerate it from the exact `level.dat` the dump came from.
- **`MATCH_CIRCUIT_DIFF` is an assumption, not a proof**: if a recipe gained a circuit or fluid
  requirement in a pack update, a pattern that used to match now silently reports
  `MATCH_CIRCUIT_DIFF` instead of the mismatch it actually has.
- **Two-step production chains are not automatically recognized**: a pattern whose output isn't
  produced by any single recipe map may simply be one half of a legitimate multi-step chain
  (e.g. an intermediate output that only becomes a "real" recipe input once combined with a
  second step). `review`/`audit` will flag it as `NO_RECIPE_FOUND`; only a human (or a
  save-specific planning script, see workflow 3) can tell a real break from an intentional
  multi-step design.
- **`gemma-review` is not reliable enough to trust unattended** (see workflow 1) — it exists as an
  optional exploration aid over the residue, never as a substitute for the deterministic verdict.
- **Player-inventory pattern support in `apply_pattern_fix.py` is implemented but lightly
  exercised** — the reference dataset this toolkit was built against had zero patterns sitting in
  a player inventory, so that code path has less real-world mileage than the region-file path.
  Always run with `--dry-run` first and inspect the printed plan before `--apply` on player data.
- **This toolkit assumes GTNH 2.9.x / Minecraft 1.7.10 / this specific AE2 fork's NBT layout**
  (`Cnt`/`Req`/`Craft` extra tags, `customName` storage, cable-part display-name storage). It will
  not work unmodified against a different pack, a different AE2 version, or a newer Minecraft
  version with different NBT structures.
