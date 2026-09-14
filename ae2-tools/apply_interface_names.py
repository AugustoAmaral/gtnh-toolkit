#!/usr/bin/env python3
"""Write AE2 interface names from interface_names_proposal.json into region files.

RUN ONLY WITH THE SERVER STOPPED. Default is --dry-run (prints every planned change, touches nothing).

Storage of names (verified against this world + AE2 rv3-beta-1050-GTNH bytecode):
  BlockInterface / fluid_interface (AE2 / AE2FC block tiles): String tag `customName` at the tile root.
  BlockCableBus parts (AE2 interface part, AE2FC dual interface part): the part's item stack display
  name, i.e. `def:<side>/tag/display/Name` inside the cable bus tile.

Region rewrite: every chunk is re-laid out sequentially (fresh offset table, timestamps preserved,
zlib-compressed payloads, 4 KiB sector padding). Untouched chunks keep their original compressed
bytes; only chunks that contain an edited tile are re-serialized. Nothing is written in place.

Usage:
  python3 apply_interface_names.py --region-dir World/DIM<id>/region [--proposal FILE]           # dry run
  python3 apply_interface_names.py --region-dir ... --apply --backup-dir /path/to/backup [--verify]
  python3 apply_interface_names.py --region-dir ... --verify                                               # check only

Requires nbtlib 2.0.4 (byte-exact round trip checked on a full dimension's chunk set).
"""
import argparse, io, json, os, shutil, struct, sys, time, zlib, gzip
import nbtlib
from nbtlib.tag import Compound, String

SECTOR = 4096


def region_of(x, z):
    return f"r.{x >> 9}.{z >> 9}.mca"


def chunk_index(x, z):
    return ((x >> 4) & 31) + ((z >> 4) & 31) * 32


def read_region(path):
    """-> (timestamps[1024], chunks{idx: (ctype, raw_payload)})"""
    data = open(path, "rb").read()
    if len(data) < 2 * SECTOR:
        return [0] * 1024, {}
    timestamps = list(struct.unpack(">1024I", data[SECTOR:2 * SECTOR]))
    chunks = {}
    for idx in range(1024):
        off = struct.unpack(">I", b"\0" + data[idx * 4:idx * 4 + 3])[0]
        if off == 0:
            continue
        p = off * SECTOR
        if p + 5 > len(data):
            raise RuntimeError(f"{path}: chunk {idx} offset past EOF")
        ln = struct.unpack(">I", data[p:p + 4])[0]
        ctype = data[p + 4]
        chunks[idx] = (ctype, data[p + 5:p + 4 + ln])
    return timestamps, chunks


def decompress(ctype, raw):
    if ctype == 2:
        return zlib.decompress(raw)
    if ctype == 1:
        return gzip.decompress(raw)
    raise RuntimeError(f"unknown chunk compression type {ctype}")


def write_region(path, timestamps, chunks):
    """Lay out all chunks sequentially starting at sector 2; write atomically via temp file."""
    offsets = bytearray(SECTOR)
    body = bytearray()
    sector = 2
    for idx in sorted(chunks):
        ctype, raw = chunks[idx]
        payload = struct.pack(">I", len(raw) + 1) + bytes([ctype]) + raw
        pad = (-len(payload)) % SECTOR
        payload += b"\0" * pad
        nsec = len(payload) // SECTOR
        if nsec > 255:
            raise RuntimeError(f"{path}: chunk {idx} needs {nsec} sectors (>255)")
        if sector > 0xFFFFFF:
            raise RuntimeError(f"{path}: region too large")
        offsets[idx * 4:idx * 4 + 4] = struct.pack(">I", (sector << 8) | nsec)
        body += payload
        sector += nsec
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(bytes(offsets))
        f.write(struct.pack(">1024I", *timestamps))
        f.write(bytes(body))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def level_of(root):
    lvl = root.get("Level")
    if lvl is None and "" in root:
        lvl = root[""].get("Level")
    return lvl


def find_tile(level, entry):
    for t in level.get("TileEntities", []):
        if int(t.get("x", 1 << 30)) == entry["x"] and int(t.get("y", -1)) == entry["y"] and int(t.get("z", 1 << 30)) == entry["z"]:
            if str(t.get("id")) != entry["te"]:
                raise RuntimeError(f"tile at {entry['x']},{entry['y']},{entry['z']} is {t.get('id')!s}, expected {entry['te']}")
            return t
    return None


def get_name(tile, entry):
    if entry["side"] is None:
        v = tile.get("customName")
        return str(v) if v is not None else None
    d = tile.get(f"def:{entry['side']}")
    if d is None:
        raise RuntimeError(f"cable bus at {entry['x']},{entry['y']},{entry['z']} has no part on side {entry['side']}")
    tag = d.get("tag")
    if tag is None:
        return None
    disp = tag.get("display")
    if disp is None or "Name" not in disp:
        return None
    return str(disp["Name"])


def set_name(tile, entry, name):
    if entry["side"] is None:
        tile["customName"] = String(name)
        return
    d = tile[f"def:{entry['side']}"]
    if "tag" not in d:
        d["tag"] = Compound()
    if "display" not in d["tag"]:
        d["tag"]["display"] = Compound()
    d["tag"]["display"]["Name"] = String(name)


def plan(entries, region_dir):
    """-> {region_file: (timestamps, chunks, {idx: new_raw}, [change dicts])}"""
    by_region = {}
    for e in entries:
        rf = region_of(e["x"], e["z"])
        if e.get("region") and e["region"] != rf:
            raise RuntimeError(f"entry {e['idx']}: region {e['region']} != computed {rf}")
        by_region.setdefault(rf, []).append(e)
    result = {}
    for rf, ents in sorted(by_region.items()):
        path = os.path.join(region_dir, rf)
        timestamps, chunks = read_region(path)
        new_raw = {}
        changes = []
        by_chunk = {}
        for e in ents:
            by_chunk.setdefault(chunk_index(e["x"], e["z"]), []).append(e)
        for idx, es in sorted(by_chunk.items()):
            if idx not in chunks:
                raise RuntimeError(f"{rf}: chunk {idx} missing (entries {[e['idx'] for e in es]})")
            ctype, raw = chunks[idx]
            root = nbtlib.File.parse(io.BytesIO(decompress(ctype, raw)))
            level = level_of(root)
            if level is None:
                raise RuntimeError(f"{rf}: chunk {idx} has no Level")
            for e in es:
                tile = find_tile(level, e)
                if tile is None:
                    raise RuntimeError(f"{rf}: chunk {idx}: no {e['te']} at {e['x']},{e['y']},{e['z']}")
                old = get_name(tile, e)
                if old == e["proposal"]:
                    changes.append({**e, "old": old, "status": "already"})
                    continue
                set_name(tile, e, e["proposal"])
                changes.append({**e, "old": old, "status": "change"})
            out = io.BytesIO()
            root.write(out)
            new_raw[idx] = (2, zlib.compress(out.getvalue()))
        result[rf] = (timestamps, chunks, new_raw, changes)
    return result


def verify(entries, region_dir):
    ok = fail = 0
    cache = {}
    for e in entries:
        rf = region_of(e["x"], e["z"])
        if rf not in cache:
            cache[rf] = read_region(os.path.join(region_dir, rf))
        timestamps, chunks = cache[rf]
        idx = chunk_index(e["x"], e["z"])
        ctype, raw = chunks[idx]
        level = level_of(nbtlib.File.parse(io.BytesIO(decompress(ctype, raw))))
        tile = find_tile(level, e)
        got = get_name(tile, e) if tile is not None else None
        if got == e["proposal"]:
            ok += 1
        else:
            fail += 1
            print(f"VERIFY FAIL idx={e['idx']} {e['te']} ({e['x']},{e['y']},{e['z']}) side={e['side']}: got {got!r}, want {e['proposal']!r}")
    print(f"verify: {ok} ok, {fail} failed, {len(entries)} entries")
    return fail == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--proposal", required=True, help="interface_names_proposal.json (e.g. build_interfaces.py output)")
    ap.add_argument("--region-dir", required=True, help="e.g. World/DIM<id>/region")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="print planned changes, write nothing (default when neither --apply nor a bare --verify is given)")
    g.add_argument("--apply", action="store_true", help="write region files (requires --backup-dir)")
    ap.add_argument("--backup-dir", help="directory that receives a copy of each .mca before it is rewritten")
    ap.add_argument("--verify", action="store_true", help="re-read the region files and confirm every proposal is present")
    args = ap.parse_args()
    if args.apply and not args.backup_dir:
        ap.error("--apply requires --backup-dir")
    verify_only = args.verify and not args.apply and not args.dry_run

    doc = json.load(open(args.proposal))
    entries = [e for e in doc["entries"] if e.get("proposal")]
    print(f"proposal: {args.proposal} ({len(doc['entries'])} entries, {len(entries)} with a name to write)")
    print(f"region dir: {args.region_dir}  mode: {'APPLY' if args.apply else ('VERIFY-ONLY' if verify_only else 'DRY-RUN')}")

    if not verify_only:
        planned = plan(entries, args.region_dir)
        nchange = 0
        for rf, (timestamps, chunks, new_raw, changes) in planned.items():
            print(f"\n{rf}: {len(chunks)} chunks, {len(new_raw)} chunk(s) to rewrite, {len(changes)} entr(y/ies)")
            for c in changes:
                nchange += c["status"] == "change"
                key = "customName" if c["side"] is None else f"def:{c['side']}/tag/display/Name"
                print(f"  [{c['status']:7}] idx={c['idx']} chunk={chunk_index(c['x'], c['z'])} {c['te']} ({c['x']},{c['y']},{c['z']}) {key}: {c['old']!r} -> {c['proposal']!r}")
        print(f"\ntotal: {nchange} name(s) to write in {sum(len(v[2]) for v in planned.values())} chunk(s) of {len(planned)} region file(s)")

        if args.apply:
            os.makedirs(args.backup_dir, exist_ok=True)
            for rf, (timestamps, chunks, new_raw, changes) in planned.items():
                if not new_raw:
                    continue
                src = os.path.join(args.region_dir, rf)
                dst = os.path.join(args.backup_dir, rf)
                if os.path.exists(dst):
                    raise RuntimeError(f"backup {dst} already exists; refusing to overwrite")
                shutil.copy2(src, dst)
                merged = dict(chunks)
                merged.update(new_raw)
                write_region(src, timestamps, merged)
                t2, c2 = read_region(src)
                if set(c2) != set(chunks) or t2 != timestamps:
                    raise RuntimeError(f"{rf}: post-write check failed (chunk set or timestamps differ)")
                print(f"wrote {src} ({len(c2)} chunks, backup at {dst})")
        else:
            print("dry run: nothing written")

    if args.verify:
        if not verify(entries, args.region_dir):
            sys.exit(1)


if __name__ == "__main__":
    main()
