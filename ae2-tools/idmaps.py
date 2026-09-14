#!/usr/bin/env python3
"""Diff FML numeric item/block id registries across `level.dat` snapshots.

Minecraft 1.7.10 (Forge/FML) assigns item/block ids per-world at first load and keeps them in
`level.dat` under `FML.ItemData` (a list of `{K: "\\x01modid:name"|"\\x00modid:name", V: id}`,
`\\x01` prefix = block, `\\x00`/absent = item). Those numeric ids are **per-world**, NOT global —
the same id can mean a different item in the client's NEI dump, a git-committed `level.dat` from
last month, and the live world. Any tool that resolves a numeric id to a name (pattern scanner,
recipe matcher, interface namer) MUST use the registry of the *exact* world/commit the ids came
from, never assume ids are stable across saves. `--out`'s `live` key is what other tools should
treat as ground truth for "this world, right now".

Usage — compare the live world against two older commits (read via `git show <rev>:World/level.dat`
piped to a temp file, or copied straight off the VPS):
    python3 idmaps.py --level b5e0d3f644=/tmp/level_b5e0d3f644.dat \\
                       --level 6d4e8cae9b=/tmp/level_6d4e8cae9b.dat \\
                       --level live=/tmp/level_live.dat \\
                       --baseline live --out idmaps.json

Prints, for every non-baseline label: GONE (id existed there, missing from baseline — item was
removed from the pack or renamed beyond recognition), NEW (in baseline, not there), RENAMED (id
maps to a different registry name in each). `--out` gets every label's full `{"item:<id>"|
"block:<id>": "modid:name"}` map, keyed by label.
"""
import argparse
import json

import nbtlib


def load(path):
    f = nbtlib.load(path)
    root = f.get("") if "" in f else f
    data = root["FML"]["ItemData"]
    m = {}
    for e in data:
        k = str(e["K"])
        v = int(e["V"])
        kind = "block" if k[0] == "\x01" else "item"
        m[(kind, v)] = k[1:]
    return m


def json_map(m):
    """{(kind, id): name} -> the `--out` shape {"item:<id>"|"block:<id>": "modid:name"}."""
    return {f"{k[0]}:{k[1]}": v for k, v in m.items()}


def build_maps(levels):
    """Importable entry point (audit.py): [(label, path)] -> {label: json_map(...)}, i.e. the `--out` JSON."""
    return {label: json_map(load(path)) for label, path in levels}


def parse_level_arg(s):
    if "=" not in s:
        raise argparse.ArgumentTypeError(f"expected LABEL=PATH, got {s!r}")
    label, path = s.split("=", 1)
    return label, path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--level", action="append", type=parse_level_arg, required=True, metavar="LABEL=PATH",
                     help="a level.dat to load, repeatable")
    ap.add_argument("--baseline", default=None,
                     help="label to diff every other level against (default: the label literally named 'live', "
                          "else the last --level given)")
    ap.add_argument("--out", default="idmaps.json", help="output JSON path (default: %(default)s)")
    args = ap.parse_args()

    maps = {label: load(path) for label, path in args.level}
    for label, m in maps.items():
        print(label, "entries", len(m), "items", sum(1 for k in m if k[0] == "item"))

    baseline = args.baseline or ("live" if "live" in maps else args.level[-1][0])
    base = maps[baseline]
    for label, m in maps.items():
        if label == baseline:
            continue
        gone = {k: v for k, v in m.items() if k not in base}
        new = {k: v for k, v in base.items() if k not in m}
        renamed = {k: (m[k], base[k]) for k in m if k in base and m[k] != base[k]}
        print(f"\n=== {label} -> {baseline}: gone={len(gone)} new={len(new)} renamed={len(renamed)}")
        for k in sorted(gone):
            print("  GONE", k, gone[k])
        for k in sorted(renamed):
            print("  RENAMED", k, renamed[k])
        for k in sorted(new):
            print("  NEW", k, new[k])

    json.dump({label: json_map(m) for label, m in maps.items()}, open(args.out, "w"))


if __name__ == "__main__":
    main()
