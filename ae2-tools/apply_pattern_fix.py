#!/usr/bin/env python3
"""Apply a fix_plan.json (delete/replace) to AE2 encoded-pattern NBT in a dimension's region
files, or World/playerdata/<name>.dat for the rare pattern sitting in a player inventory.
RUN ONLY WITH THE SERVER STOPPED. Default is --dry-run (touches nothing).

Pattern identity: (te, x, y, z, path) from a scan_patterns.py run (patterns.json[.gz]).
`path` mirrors scan_patterns.py's walk(): "/key" descends a Compound, "[N]" (chained, no leading
slash) indexes a List at the current position — e.g. "/extra:2/patterns/#0" or "/Contents[0]".

fix_plan.json is a JSON array of:
  {"id": int, "te", "x", "y", "z", "path", "action": "delete"|"replace",
   "reason": "<free text>", "human": "<free text>", "source": "<recipe id or 'note'>",
   "old": <stack-shape>, "new": <stack-shape>|null}
stack-shape (scan_patterns.py's shape, NOT raw NBT):
  {"item_id": int, "count": int, "crafting": 0|1, "substitute": 0|1,
   "in": [ingredient|null, ...], "out": [ingredient|null, ...]}
  ingredient = {"id": int, "dmg": int, "n": int, "tag": <json>|null, "extra"?: {...}}
             | {"raw": {...}}   (AE2FC fluid drops, no "id" key)
"new" is null for a delete. delete clears the slot the way this AE2 fork actually stores empty
slots: the "#N" key is removed from a `patterns` Compound (verified empirically against a full
dimension dump: zero patterns Compounds with an empty-`{}` entry — populated keys only), or the
list element is removed from a plain-list inventory (e.g. `Contents[N]`, verified against the
compact chest: each entry carries its own "Slot" tag, so removing the array element does not
touch sibling slots).

List-indexed paths (only `Contents[N]` in the reference dataset) are re-anchored to the element's
"Slot" tag (read once from a pristine dump_tes.py snapshot) instead of the raw positional index,
so multiple edits to the same list — and re-running --verify as a separate process after --apply
already shifted the list — stay correct. Compound-keyed paths ("#N", "extra:N", "def:N") never
reindex and are looked up directly by key.

Region rewrite: proven core ported from apply_interface_names.py in this same toolkit — every
chunk is re-laid out sequentially (fresh offset table, timestamps preserved, zlib-compressed
payloads, 4 KiB sector padding), atomic temp+rename, untouched chunks keep their original
compressed bytes verbatim.

Usage:
  python3 apply_pattern_fix.py --region-dir World/DIM<id>/region                    # dry run
  python3 apply_pattern_fix.py --region-dir ... --apply --backup-dir /path/to/backup [--verify]
  python3 apply_pattern_fix.py --region-dir ... --verify                            # check only

Requires nbtlib 2.0.4 (byte-exact round trip already checked against a full dimension's chunk
set).
"""
import argparse
import gzip
import io
import json
import os
import re
import shutil
import struct
import sys
import zlib

import nbtlib
from nbtlib.tag import Byte, Compound, Double, Float, Int, List, Long, Short, String

SECTOR = 4096

KNOWN_INGREDIENT_EXTRA_TYPES = {
    # observed across a full scan of the reference dataset (StackType is a String, never
    # reconstructed here because "raw" ingredients are rebuilt whole via json_to_compound).
    "Cnt": Long,
    "Req": Long,
    "Craft": Byte,
}

DEFAULT_ZERO_BYTE_FIELDS = ("crafting", "substitute")  # tag-level fields with an implicit 0 default


# ---------------------------------------------------------------------------
# Region I/O — ported verbatim (logic) from apply_interface_names.py (same toolkit, proven).
# ---------------------------------------------------------------------------

def region_of(x, z):
    return f"r.{x >> 9}.{z >> 9}.mca"


def chunk_index(x, z):
    return ((x >> 4) & 31) + ((z >> 4) & 31) * 32


def read_region(path):
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
        if p + 4 + ln > len(data):
            raise RuntimeError(f"{path}: chunk {idx} payload past EOF")
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
        if (int(t.get("x", 1 << 30)) == entry["x"] and int(t.get("y", -1)) == entry["y"]
                and int(t.get("z", 1 << 30)) == entry["z"]):
            if str(t.get("id")) != entry["te"]:
                raise RuntimeError(
                    f"id={entry['id']}: tile at {entry['x']},{entry['y']},{entry['z']} "
                    f"is {t.get('id')!s}, expected {entry['te']}")
            return t
    return None


# ---------------------------------------------------------------------------
# NBT path navigation: mirrors scan_cnt.py's walk() path grammar exactly.
# ---------------------------------------------------------------------------

PATH_RE = re.compile(r'/([^/\[]+)|\[(\d+)\]')


def parse_path(path):
    steps = []
    for m in PATH_RE.finditer(path):
        if m.group(1) is not None:
            steps.append(("key", m.group(1)))
        else:
            steps.append(("idx", int(m.group(2))))
    rebuilt = "".join(("/" + k if kind == "key" else f"[{k}]") for kind, k in steps)
    if rebuilt != path or not steps:
        raise RuntimeError(f"path {path!r} did not round-trip through the parser (got {rebuilt!r})")
    return steps


def navigate_to_parent(root, steps):
    """Descend every step except the last; return (parent_container, last_kind, last_key)."""
    cur = root
    for kind, key in steps[:-1]:
        cur = cur[key]
    last_kind, last_key = steps[-1]
    return cur, last_kind, last_key


# ---------------------------------------------------------------------------
# Slot hints: re-anchor list-indexed paths ("Contents[N]") to a stable "Slot" tag captured from the
# pristine 09-13 dump, so re-ordering caused by earlier deletes never invalidates a later lookup.
# ---------------------------------------------------------------------------

def build_slot_hints(dump_tes_path):
    """-> {(te, x, y, z, container_path_prefix): {orig_index: slot_value}}"""
    hints = {}

    def walk(node, ctx, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, ctx, path + "/" + str(k))
        elif isinstance(node, list):
            if node and all(isinstance(x, dict) and "Slot" in x for x in node):
                key = (ctx["te"], ctx["x"], ctx["y"], ctx["z"], path)
                hints[key] = {i: int(x["Slot"]) for i, x in enumerate(node)}
            for i, x in enumerate(node):
                walk(x, ctx, path + f"[{i}]")

    with gzip.open(dump_tes_path, "rt") as f:
        doc = json.load(f)
    for t in doc["tes"]:
        ctx = {"te": str(t.get("id")), "x": int(t.get("x", 0)), "y": int(t.get("y", 0)), "z": int(t.get("z", 0))}
        walk(t, ctx, "")
    return hints


def container_path_prefix(path, steps):
    """path with its final "[N]" (or "/key") step stripped, for slot-hint lookup."""
    last_kind, last_key = steps[-1]
    if last_kind == "idx":
        suffix = f"[{last_key}]"
    else:
        suffix = "/" + last_key
    assert path.endswith(suffix)
    return path[: -len(suffix)]


def locate(entry, root, slot_hints):
    """-> (container, kind, key_to_use_now) ready for get/set/del, re-anchored via Slot when the
    final step is a list index and a hint is available; returns key=None (kind='idx-missing') if a
    slot-anchored delete target is already gone (expected when --verify runs after --apply)."""
    steps = parse_path(entry["path"])
    container, kind, key = navigate_to_parent(root, steps)
    if kind == "idx":
        prefix = container_path_prefix(entry["path"], steps)
        hint_key = (entry["te"], entry["x"], entry["y"], entry["z"], prefix)
        hint = slot_hints.get(hint_key)
        if hint is not None and key in hint:
            want_slot = hint[key]
            found = None
            for i, elem in enumerate(container):
                if isinstance(elem, Compound) and "Slot" in elem and int(elem["Slot"]) == want_slot:
                    found = i
                    break
            if found is None:
                return container, "idx-missing", None
            return container, "idx", found
        # no hint (element without a Slot tag): fall back to the raw positional index, as scanned.
    return container, kind, key


# ---------------------------------------------------------------------------
# NBT <-> plan-shape conversion (scan_cnt.py's stack-summary shape).
# ---------------------------------------------------------------------------

def json_tag(v):
    if isinstance(v, Compound):
        return {k: json_tag(x) for k, x in v.items()}
    if isinstance(v, List):
        return [json_tag(x) for x in v]
    if isinstance(v, (bytes, bytearray)):
        return {"__bytes": len(v)}
    return v.unpack() if hasattr(v, "unpack") else v


def stack_shape(c):
    if "id" not in c:
        return {"raw": json_tag(c)}
    d = {"id": int(c["id"]), "dmg": int(c.get("Damage", 0)), "n": int(c.get("Count", 1)),
         "tag": (json_tag(c["tag"]) if "tag" in c else None)}
    extra = {k: json_tag(x) for k, x in c.items() if k not in ("id", "Damage", "Count", "tag")}
    if extra:
        d["extra"] = extra
    return d


def canonical_stack(pattern_compound):
    """Reproduce scan_cnt.py's found.append(...) shape for a single already-located pattern stack."""
    t = pattern_compound.get("tag", Compound())
    return {
        "item_id": int(pattern_compound["id"]),
        "count": int(pattern_compound.get("Count", 1)),
        "crafting": int(t.get("crafting", 0)),
        "substitute": int(t.get("substitute", 0)),
        "in": [stack_shape(x) if len(x) else None for x in t["in"]],
        "out": [stack_shape(x) if len(x) else None for x in t["out"]],
    }


def infer_scalar(v, key_hint=None):
    if key_hint in ("id", "Damage"):
        return Short(v)
    if key_hint == "Count":
        return Byte(v)
    if isinstance(v, bool):
        raise RuntimeError(f"cannot infer NBT type for bool value under key {key_hint!r}")
    if isinstance(v, int):
        return Int(v)
    if isinstance(v, float):
        return Double(v)
    if isinstance(v, str):
        return String(v)
    raise RuntimeError(f"cannot infer NBT type for value {v!r} (key {key_hint!r})")


def json_to_tag(v, hint_v, key_hint=None):
    if isinstance(v, dict):
        hint_c = hint_v if isinstance(hint_v, Compound) else None
        return json_to_compound(v, hint_c)
    if isinstance(v, list):
        hint_l = hint_v if isinstance(hint_v, List) else None
        items = [json_to_tag(x, hint_l[i] if (hint_l is not None and i < len(hint_l)) else None)
                  for i, x in enumerate(v)]
        if hint_l is not None:
            return List[hint_l.subtype](items)
        if items:
            return List[type(items[0])](items)
        raise RuntimeError("cannot infer element type for an empty list with no hint")
    if hint_v is not None and isinstance(hint_v, (Byte, Short, Int, Long, Float, Double, String)):
        return type(hint_v)(v)
    return infer_scalar(v, key_hint)


def json_to_compound(d, hint):
    c = Compound()
    for k, v in d.items():
        hint_v = hint[k] if (hint is not None and k in hint) else None
        c[k] = json_to_tag(v, hint_v, key_hint=k)
    return c


def build_ingredient(live_c, new_shape):
    if "raw" in new_shape:
        return json_to_compound(new_shape["raw"], live_c)
    c = Compound()
    c["id"] = Short(new_shape["id"])
    c["Damage"] = Short(new_shape["dmg"])
    c["Count"] = Byte(new_shape["n"])
    extra = new_shape.get("extra")
    if extra:
        for k, v in extra.items():
            if k not in KNOWN_INGREDIENT_EXTRA_TYPES:
                raise RuntimeError(
                    f"unknown extra ingredient key {k!r}={v!r}; add its NBT type to "
                    f"KNOWN_INGREDIENT_EXTRA_TYPES before applying")
            c[k] = KNOWN_INGREDIENT_EXTRA_TYPES[k](v)
    if new_shape.get("tag") is not None:
        old_tag_live = live_c["tag"] if (live_c is not None and "tag" in live_c) else None
        c["tag"] = json_to_compound(new_shape["tag"], old_tag_live)
    return c


def build_stack_list(live_list, old_list, new_list, label):
    if len(old_list) != len(live_list) or len(new_list) != len(live_list):
        raise RuntimeError(
            f"{label}: slot count mismatch (live={len(live_list)}, plan old={len(old_list)}, "
            f"plan new={len(new_list)}) — refusing to guess a new pattern-grid size")
    result = []
    for i in range(len(live_list)):
        old_s, new_s, live_c = old_list[i], new_list[i], live_list[i]
        if new_s == old_s:
            result.append(live_c)          # unchanged slot: keep the exact original NBT object
        elif new_s is None:
            result.append(Compound())       # cleared slot: verified empty-slot marker in this fork
        else:
            result.append(build_ingredient(live_c if old_s is not None else None, new_s))
    return List[Compound](result)


def build_replacement(live, new_shape):
    """Mutate `live` (the pattern's own itemstack Compound) in place to match new_shape."""
    live["id"] = Short(new_shape["item_id"])
    live["Count"] = Byte(new_shape["count"])
    tag = live["tag"]
    for field in DEFAULT_ZERO_BYTE_FIELDS:
        new_val = new_shape[field]
        had_key = field in tag
        if had_key or new_val != 0:
            tag[field] = Byte(new_val)
    new_in = build_stack_list(tag["in"], canonical_in_out(tag, "in"), new_shape["in"], "in")
    new_out = build_stack_list(tag["out"], canonical_in_out(tag, "out"), new_shape["out"], "out")
    tag["in"] = new_in
    tag["out"] = new_out
    if "Inputs" in tag:
        tag["Inputs"] = new_in     # this AE2 fork keeps a denormalized Inputs/Outputs mirror of
    if "Outputs" in tag:            # in/out in some patterns (verified equal content on read);
        tag["Outputs"] = new_out    # keep both in sync so no stale decoded cache survives the fix.
    return live


def canonical_in_out(tag, key):
    return [stack_shape(x) if len(x) else None for x in tag[key]]

def copy_nbt(v):
    """Cheap structural copy: rebuilds Compound/List wrappers, reuses (immutable) leaf tag
    instances. Used so dry-run can attempt a real build_replacement() (catching type/overflow
    errors before any write) without mutating the live tree."""
    if isinstance(v, Compound):
        return Compound({k: copy_nbt(x) for k, x in v.items()})
    if isinstance(v, List):
        return List[v.subtype]([copy_nbt(x) for x in v])
    return v


# ---------------------------------------------------------------------------
# Player-inventory support (not exercised by the reference dataset — no patterns were sitting in
# a player inventory at scan time — but kept for completeness per the task contract).
# ---------------------------------------------------------------------------

def is_player_entry(entry):
    return entry["te"] == "player" or str(entry.get("where", "")).startswith("player:")


def player_dat_path(entry, playerdata_dir):
    name = str(entry["where"]).split(":", 1)[1]
    return os.path.join(playerdata_dir, name + ".dat")


def load_player_root(path):
    doc = nbtlib.load(path)
    return doc[""] if "" in doc else doc


def write_player_file(path, root_doc):
    tmp = path + ".tmp"
    root_doc.write(tmp)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Plan execution
# ---------------------------------------------------------------------------

def group_by_region_and_chunk(entries):
    by_region = {}
    for e in entries:
        if is_player_entry(e):
            continue
        rf = region_of(e["x"], e["z"])
        by_region.setdefault(rf, {}).setdefault(chunk_index(e["x"], e["z"]), []).append(e)
    return by_region


def order_for_mutation(entries, slot_hints):
    """Sort so that, within any single live-list container, higher (re-anchored) indices are
    processed before lower ones — the standard safe order for shifting deletions."""
    tagged = []
    for e in entries:
        # container identity resolved lazily inside apply; here we only need a stable sort key
        # derived from the ORIGINAL path index for the (rare) case multiple actions share a list.
        steps = parse_path(e["path"])
        last_kind, last_key = steps[-1]
        tagged.append((e, last_key if last_kind == "idx" else -1))
    tagged.sort(key=lambda t: -t[1])
    return [e for e, _ in tagged]


def process_chunk(level, entries, slot_hints, changes, errors, dry_run):
    by_te = {}
    for e in entries:
        by_te.setdefault((e["te"], e["x"], e["y"], e["z"]), []).append(e)
    for (te_id, x, y, z), acts in by_te.items():
        tile = find_tile(level, {"te": te_id, "x": x, "y": y, "z": z, "id": acts[0]["id"]})
        if tile is None:
            for a in acts:
                errors.append(f"id={a['id']}: no {te_id} tile entity at ({x},{y},{z})")
            continue
        for e in order_for_mutation(acts, slot_hints):
            try:
                container, kind, key = locate(e, tile, slot_hints)
                if kind == "idx-missing":
                    if e["action"] == "delete":
                        changes.append({**e, "status": "already-deleted"})
                        continue
                    raise RuntimeError("list element already gone (Slot not found) but action is not delete")
                live = container[key]
                live_shape = canonical_stack(live)
                if live_shape != e["old"]:
                    raise RuntimeError(
                        "live stack does not match plan 'old':\n"
                        f"  live: {json.dumps(live_shape)}\n"
                        f"  plan: {json.dumps(e['old'])}")
                if e["action"] == "delete":
                    if e["new"] is not None:
                        raise RuntimeError("action=delete but plan 'new' is not null")
                    if not dry_run:
                        del container[key]
                    changes.append({**e, "status": "deleted"})
                elif e["action"] == "replace":
                    if e["new"] is None:
                        raise RuntimeError("action=replace but plan 'new' is null")
                    build_replacement(live if not dry_run else copy_nbt(live), e["new"])
                    changes.append({**e, "status": "replaced"})
                else:
                    raise RuntimeError(f"unknown action {e['action']!r}")
            except Exception as ex:
                errors.append(f"id={e['id']} ({e['te']} at {e['x']},{e['y']},{e['z']} {e['path']}): {ex}")


def process_player_entries(entries, playerdata_dir, changes, errors, dry_run):
    """Validate + (if not dry_run) mutate every player-inventory action. Returns {path: root_doc}
    for every playerdata file touched, so the caller can back it up and write it once validation
    of the WHOLE plan (world + player) has passed."""
    by_file = {}
    for e in entries:
        by_file.setdefault(player_dat_path(e, playerdata_dir), []).append(e)
    roots = {}
    for path, acts in by_file.items():
        if not os.path.exists(path):
            for a in acts:
                errors.append(f"id={a['id']}: playerdata file not found: {path}")
            continue
        root = load_player_root(path)
        roots[path] = root
        for e in acts:
            try:
                container, kind, key = locate(e, root, {})  # no Contents-style list in this dataset
                live = container[key]
                live_shape = canonical_stack(live)
                if live_shape != e["old"]:
                    raise RuntimeError(
                        f"live stack does not match plan 'old':\n  live: {json.dumps(live_shape)}\n"
                        f"  plan: {json.dumps(e['old'])}")
                if e["action"] == "delete":
                    if not dry_run:
                        del container[key]
                    changes.append({**e, "status": "deleted"})
                elif e["action"] == "replace":
                    if e["new"] is None:
                        raise RuntimeError("action=replace but plan 'new' is null")
                    build_replacement(live if not dry_run else copy_nbt(live), e["new"])
                    changes.append({**e, "status": "replaced"})
                else:
                    raise RuntimeError(f"unknown action {e['action']!r}")
            except Exception as ex:
                errors.append(f"id={e['id']} (player {path} {e['path']}): {ex}")
    return roots



def human_diff_line(c):
    where = f"{c['te']} at ({c['x']},{c['y']},{c['z']}) {c['path']}"
    if c["status"] in ("deleted", "already-deleted"):
        return f"[DELETE ] {where}: {c.get('human', '')} — {c['reason']}"
    return f"[REWRITE] {where}: {c.get('human', '')} — {c['reason']}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", required=True, help="fix_plan.json (FixPlanner output)")
    ap.add_argument("--dump-tes", required=True,
                     help="pristine TE dump used to derive Slot hints for list-indexed paths (dump_tes.py output, same dim, taken before any fix is applied)")
    ap.add_argument("--region-dir", required=True, help="e.g. World/DIM<id>/region")
    ap.add_argument("--playerdata-dir", default=None, help="e.g. World/playerdata (only if the plan touches a player inventory)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true", help="write region/playerdata files (requires --backup-dir)")
    ap.add_argument("--backup-dir", help="directory that receives a copy of each file before it is rewritten")
    ap.add_argument("--verify", action="store_true", help="re-read the files and confirm every planned change is present")
    args = ap.parse_args()
    if args.apply and not args.backup_dir:
        ap.error("--apply requires --backup-dir")
    verify_only = args.verify and not args.apply and not args.dry_run

    entries = json.load(open(args.plan))
    print(f"plan: {args.plan} ({len(entries)} actions)")
    print(f"region dir: {args.region_dir}  mode: {'APPLY' if args.apply else ('VERIFY-ONLY' if verify_only else 'DRY-RUN')}")

    slot_hints = build_slot_hints(args.dump_tes)
    player_entries = [e for e in entries if is_player_entry(e)]
    world_entries = [e for e in entries if not is_player_entry(e)]
    if player_entries and not args.playerdata_dir:
        ap.error(f"plan has {len(player_entries)} player-inventory action(s); --playerdata-dir is required")

    if not verify_only:
        by_region = group_by_region_and_chunk(world_entries)

        # ---- Phase 1: validate the WHOLE plan (every region + every playerdata file) against the
        # live NBT, mutating nothing. Nothing is written unless this entire phase is error-free.
        changes, errors = [], []
        for rf in sorted(by_region):
            path = os.path.join(args.region_dir, rf)
            timestamps, chunks = read_region(path)
            for idx, ents in sorted(by_region[rf].items()):
                if idx not in chunks:
                    for e in ents:
                        errors.append(f"id={e['id']}: {rf} chunk {idx} missing entirely")
                    continue
                ctype, raw = chunks[idx]
                root = nbtlib.File.parse(io.BytesIO(decompress(ctype, raw)))
                level = level_of(root)
                if level is None:
                    for e in ents:
                        errors.append(f"id={e['id']}: {rf} chunk {idx} has no Level tag")
                    continue
                process_chunk(level, ents, slot_hints, changes, errors, dry_run=True)
        if player_entries:
            process_player_entries(player_entries, args.playerdata_dir, changes, errors, dry_run=True)

        if errors:
            print(f"\n{len(errors)} ERROR(S) — refusing to write anything:")
            for err in errors:
                print(f"  {err}")
            sys.exit(1)

        for c in changes:
            print("  " + human_diff_line(c))
        print(f"\ntotal: {len(changes)} action(s) ({sum(1 for c in changes if c['status'] in ('deleted', 'already-deleted'))} delete, "
              f"{sum(1 for c in changes if c['status'] == 'replaced')} replace) across "
              f"{len(by_region)} region file(s){f', {len(player_entries)} in playerdata' if player_entries else ''}")

        if not args.apply:
            print("dry run: nothing written")
        else:
            # ---- Phase 2: validation passed for the entire plan — now actually write, one file
            # at a time, each preceded by its own backup copy.
            n_regions_touched = n_chunks_touched = 0
            for rf in sorted(by_region):
                path = os.path.join(args.region_dir, rf)
                timestamps, chunks = read_region(path)
                new_raw = {}
                for idx, ents in sorted(by_region[rf].items()):
                    ctype, raw = chunks[idx]
                    root = nbtlib.File.parse(io.BytesIO(decompress(ctype, raw)))
                    level = level_of(root)
                    write_errors = []
                    process_chunk(level, ents, slot_hints, [], write_errors, dry_run=False)
                    if write_errors:
                        raise RuntimeError(f"{rf} chunk {idx}: re-validation failed at write time: {write_errors}")
                    out = io.BytesIO()
                    root.write(out)
                    new_raw[idx] = (2, zlib.compress(out.getvalue()))
                    n_chunks_touched += 1
                os.makedirs(args.backup_dir, exist_ok=True)
                dst = os.path.join(args.backup_dir, rf)
                if os.path.exists(dst):
                    raise RuntimeError(f"backup {dst} already exists; refusing to overwrite")
                shutil.copy2(path, dst)
                merged = dict(chunks)
                merged.update(new_raw)
                write_region(path, timestamps, merged)
                t2, c2 = read_region(path)
                if set(c2) != set(chunks) or t2 != timestamps:
                    raise RuntimeError(f"{rf}: post-write check failed (chunk set or timestamps differ)")
                n_regions_touched += 1
                print(f"wrote {path} ({len(c2)} chunks, {len(new_raw)} rewritten, backup at {dst})")
            print(f"\napplied to {n_regions_touched} region file(s), {n_chunks_touched} chunk(s) rewritten")

            if player_entries:
                write_errors = []
                roots = process_player_entries(player_entries, args.playerdata_dir, [], write_errors, dry_run=False)
                if write_errors:
                    raise RuntimeError(f"playerdata re-validation failed at write time: {write_errors}")
                os.makedirs(args.backup_dir, exist_ok=True)
                for path, root in roots.items():
                    dst = os.path.join(args.backup_dir, os.path.basename(path))
                    if os.path.exists(dst):
                        raise RuntimeError(f"backup {dst} already exists; refusing to overwrite")
                    shutil.copy2(path, dst)
                    write_player_file(path, root)
                    print(f"wrote {path} (backup at {dst})")

    if args.verify:
        ok = fail = 0
        cache = {}
        for e in world_entries:
            rf = region_of(e["x"], e["z"])
            if rf not in cache:
                cache[rf] = read_region(os.path.join(args.region_dir, rf))
            timestamps, chunks = cache[rf]
            idx = chunk_index(e["x"], e["z"])
            ctype, raw = chunks[idx]
            level = level_of(nbtlib.File.parse(io.BytesIO(decompress(ctype, raw))))
            tile = find_tile(level, {"te": e["te"], "x": e["x"], "y": e["y"], "z": e["z"], "id": e["id"]})
            try:
                if tile is None:
                    raise RuntimeError("tile entity gone")
                container, kind, key = locate(e, tile, slot_hints)
                verify_one(e, container, kind, key)
                ok += 1
            except Exception as ex:
                fail += 1
                print(f"VERIFY FAIL id={e['id']} {e['te']} ({e['x']},{e['y']},{e['z']}) {e['path']}: {ex}")
        for e in player_entries:
            path = player_dat_path(e, args.playerdata_dir)
            root = load_player_root(path)
            try:
                container, kind, key = locate(e, root, {})
                verify_one(e, container, kind, key)
                ok += 1
            except Exception as ex:
                fail += 1
                print(f"VERIFY FAIL id={e['id']} player {path} {e['path']}: {ex}")
        print(f"\nverify: {ok} ok, {fail} failed, {len(world_entries) + len(player_entries)} total actions")
        if fail:
            sys.exit(1)


def verify_one(e, container, kind, key):
    """Raise if entry `e`'s expected post-fix state is not what's on disk right now."""
    gone = (kind == "idx-missing") or (kind == "key" and key not in container) or (kind == "idx" and key >= len(container))
    if e["action"] == "delete":
        if not gone:
            raise RuntimeError(f"still present: {json.dumps(canonical_stack(container[key]))}")
    else:
        if gone:
            raise RuntimeError("replaced element vanished")
        got = canonical_stack(container[key])
        if got != e["new"]:
            raise RuntimeError(f"mismatch: got {json.dumps(got)}, want {json.dumps(e['new'])}")


if __name__ == "__main__":
    main()
