#!/usr/bin/env python3
"""Scan region files (+ optional player data) for AE2 Encoded Patterns / AE2FC fluid patterns /
Ultimate Patterns and dump every one found to a single JSON.

An "encoded pattern" is any ItemStack whose NBT has `id`, `Count`, and a `tag` compound with both
`in` and `out` lists — that shape is unique to AE2's `ItemEncodedPattern` and its AE2FC/Ultimate
variants, so no item-id allowlist is needed (robust across id churn between GTNH versions).

Count-aware: this GTNH AE2 fork stores real stack sizes above 127 (and fluid mB amounts) in a
`Cnt` **long** tag with `Count` left at 0 or 1 — a plain `Count` reader silently loses those
amounts. This scanner keeps every extra key of each stack (`Cnt`, `Req`, `Craft`, ...) under an
`"extra"` dict so nothing is lost; consumers should prefer `extra.Cnt` over `n` (=`Count`) when
present. This supersedes the two-script split from 2026-09-12 (`scan.py` dropped `Cnt`; `scan_cnt.py`
kept it) — this file is the `scan_cnt.py` behaviour, now the only scanner.

Usage:
    python3 scan_patterns.py World/DIM<id> --players World/playerdata --out patterns.json

Positional args are region-containing dimension directories (each must have a `region/` subdir);
pass as many as needed. `--players` is optional (glob of `*.dat` files, e.g. inventories holding
patterns pulled out of the ME system and stored elsewhere).

Output: JSON list of {where, te, x, y, z, path, item_id, crafting, substitute, in, out}, `in`/`out`
being lists of stacks `{id, dmg, n, tag, extra?}` (or `null` for an empty slot), 1:1 aligned to the
pattern's inventory. Identity for later tools = `(te, x, y, z, path)`.
"""
import argparse
import glob
import gzip
import io
import json
import os
import struct
import sys
import zlib

import nbtlib
from nbtlib.tag import Compound, List


def json_tag(v):
    if isinstance(v, Compound):
        return {k: json_tag(x) for k, x in v.items()}
    if isinstance(v, List):
        return [json_tag(x) for x in v]
    if isinstance(v, (bytes, bytearray)):
        return {"__bytes": len(v)}
    try:
        return v.unpack() if hasattr(v, "unpack") else v
    except Exception:
        return str(v)


def stack(c):
    if "id" not in c:
        return {"raw": json_tag(c)}
    d = {"id": int(c["id"]), "dmg": int(c.get("Damage", 0)), "n": int(c.get("Count", 1)),
         "tag": (json_tag(c["tag"]) if "tag" in c else None)}
    extra = {k: json_tag(x) for k, x in c.items() if k not in ("id", "Damage", "Count", "tag")}
    if extra:
        d["extra"] = extra
    return d


def walk(v, ctx, path, found):
    if isinstance(v, Compound):
        if ("id" in v and "Count" in v and "tag" in v and isinstance(v["tag"], Compound)
                and "in" in v["tag"] and "out" in v["tag"]):
            t = v["tag"]
            found.append({**ctx, "path": path, "item_id": int(v["id"]), "count": int(v["Count"]),
                          "crafting": int(t.get("crafting", 0)), "substitute": int(t.get("substitute", 0)),
                          "in": [stack(x) if len(x) else None for x in t["in"]],
                          "out": [stack(x) if len(x) else None for x in t["out"]]})
            return
        for k, x in v.items():
            walk(x, ctx, path + "/" + str(k), found)
    elif isinstance(v, List):
        for i, x in enumerate(v):
            walk(x, ctx, path + f"[{i}]", found)


def scan_dim(d, label, found):
    nch = 0
    bad = 0
    for fn in sorted(glob.glob(os.path.join(d, "region", "r.*.mca"))):
        data = open(fn, "rb").read()
        if len(data) < 8192:
            continue
        for idx in range(1024):
            off = struct.unpack(">I", b"\0" + data[idx * 4:idx * 4 + 3])[0]
            if off == 0:
                continue
            p = off * 4096
            if p + 5 > len(data):
                continue
            ln = struct.unpack(">I", data[p:p + 4])[0]
            ct = data[p + 4]
            raw = data[p + 5:p + 4 + ln]
            try:
                blob = zlib.decompress(raw) if ct == 2 else (gzip.decompress(raw) if ct == 1 else raw)
            except Exception:
                bad += 1
                continue
            if b"TileEntities" not in blob and b"Entities" not in blob:
                continue
            try:
                root = nbtlib.File.parse(io.BytesIO(blob))
            except Exception:
                bad += 1
                continue
            lvl = root.get("Level") or root.get("", {}).get("Level")
            if lvl is None:
                continue
            nch += 1
            for t in lvl.get("TileEntities", []):
                ctx = {"where": label, "te": str(t.get("id")), "x": int(t.get("x", 0)),
                       "y": int(t.get("y", 0)), "z": int(t.get("z", 0))}
                if "mID" in t:
                    ctx["mID"] = int(t["mID"])
                walk(t, ctx, "", found)
            for e in lvl.get("Entities", []):
                pos = e.get("Pos", [0, 0, 0])
                ctx = {"where": label, "te": "entity:" + str(e.get("id")), "x": int(pos[0]),
                       "y": int(pos[1]), "z": int(pos[2])}
                walk(e, ctx, "", found)
    print(label, "chunks", nch, "bad", bad, "patterns so far", len(found), file=sys.stderr)


def scan(dims, players=None):
    """Importable entry point (audit.py): -> the same list of patterns `--out` would hold.
    `dims` = dimension directories (each with a region/ subdir), `players` = playerdata dir or None."""
    found = []
    for d in dims:
        scan_dim(d, os.path.basename(d.rstrip("/")) or "World", found)
    if players:
        for fn in sorted(glob.glob(os.path.join(players, "*.dat"))):
            root = nbtlib.load(fn)
            r = root.get("") if "" in root else root
            walk(r, {"where": "player:" + os.path.basename(fn)[:-4], "te": "player", "x": 0, "y": 0, "z": 0}, "", found)
    return found


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dims", nargs="+", help="dimension directories to scan (each with a region/ subdir)")
    ap.add_argument("--players", default=None, help="directory of playerdata/*.dat to also scan (optional)")
    ap.add_argument("--out", default="patterns.json", help="output JSON path (default: %(default)s)")
    args = ap.parse_args()

    found = scan(args.dims, args.players)
    print("done, patterns", len(found), file=sys.stderr)
    json.dump(found, open(args.out, "w"))


if __name__ == "__main__":
    main()
