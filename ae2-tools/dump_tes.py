#!/usr/bin/env python3
"""Dump every TileEntity of a Minecraft region directory (Anvil `.mca`) to a single JSON.gz.

Reads raw `region/*.mca` files directly (no `mcedit`/`amulet`/server needed), decompresses each
chunk, parses its NBT with `nbtlib`, and collects every `TileEntities` entry as plain JSON,
tagging each with the region file name and chunk-table index it came from (`_region`/`_chunk`
— needed later to locate the exact chunk for a write, e.g. by `apply_interface_names.py` or
`apply_pattern_fix.py`). Binary NBT arrays are summarised as `{"__array_len": N}` (not needed for
read-only analysis and bulky); byte arrays as `{"__bytes": N}`.

Read-only: never writes into the world. Safe to run against a live server (a chunk being saved
mid-read just gets skipped and counted in `bad`) or, for reproducible results, a stopped one.

Usage:
    python3 dump_tes.py --region-dir World/DIM<id>/region --out dump_tes.json.gz

Output JSON shape: {"region_dir", "files", "chunks", "bad": [[file,idx,reason],...], "tes": [...]}
Generalized to take any region dir on the command line, rather than a hardcoded dimension list.
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
from nbtlib.tag import ByteArray, Compound, IntArray, List, LongArray


def clean(v):
    if isinstance(v, (ByteArray, IntArray, LongArray)):
        return {"__array_len": len(v)}
    if isinstance(v, Compound):
        return {k: clean(x) for k, x in v.items()}
    if isinstance(v, List):
        return [clean(x) for x in v]
    if isinstance(v, (bytes, bytearray)):
        return {"__bytes": len(v)}
    try:
        return v.unpack() if hasattr(v, "unpack") else v
    except Exception:
        return str(v)


def dump_region_dir(region_dir):
    """-> dict with files/chunks/bad/tes, exactly the on-disk JSON shape."""
    out = []
    nch = 0
    bad = []
    nfiles = 0
    for fn in sorted(glob.glob(os.path.join(region_dir, "r.*.mca"))):
        data = open(fn, "rb").read()
        if len(data) < 8192:
            continue
        nfiles += 1
        for idx in range(1024):
            off = struct.unpack(">I", b"\0" + data[idx * 4:idx * 4 + 3])[0]
            if off == 0:
                continue
            p = off * 4096
            if p + 5 > len(data):
                bad.append((os.path.basename(fn), idx, "offset past EOF"))
                continue
            ln = struct.unpack(">I", data[p:p + 4])[0]
            ct = data[p + 4]
            raw = data[p + 5:p + 4 + ln]
            try:
                blob = zlib.decompress(raw) if ct == 2 else (gzip.decompress(raw) if ct == 1 else raw)
            except Exception as e:
                bad.append((os.path.basename(fn), idx, "decompress: " + str(e)))
                continue
            try:
                root = nbtlib.File.parse(io.BytesIO(blob))
            except Exception as e:
                bad.append((os.path.basename(fn), idx, "nbt: " + str(e)))
                continue
            lvl = root.get("Level") or root.get("", {}).get("Level")
            if lvl is None:
                continue
            nch += 1
            for t in lvl.get("TileEntities", []):
                c = clean(t)
                c["_region"] = os.path.basename(fn)
                c["_chunk"] = idx
                out.append(c)
    return {"region_dir": region_dir, "files": nfiles, "chunks": nch, "bad": bad, "tes": out}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region-dir", required=True,
                     help="directory with r.*.mca files, e.g. World/DIM<id>/region")
    ap.add_argument("--out", default="dump_tes.json.gz", help="output path (default: %(default)s)")
    args = ap.parse_args()

    result = dump_region_dir(args.region_dir)
    with gzip.open(args.out, "wt") as f:
        json.dump(result, f)
    print("files", result["files"], "chunks", result["chunks"], "TEs", len(result["tes"]),
          "bad", len(result["bad"]), flush=True)
    for b in result["bad"]:
        print("BAD", *b)


if __name__ == "__main__":
    main()
