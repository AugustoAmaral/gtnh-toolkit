#!/usr/bin/env python3
"""Build an inventory + name proposal for every AE2/AE2FC ME Interface in a dump_tes.py dump.

Recovered/reconstructed from an interactive session that produced the reference interface
inventory + name proposal (this script did not exist as a standalone file before — it was ~45
refined kernel cells). Re-run against the same inputs it reproduces that reference dataset
byte-for-byte in every count that matters (interfaces per type, name proposals, control-group
agreement, circuit agreement) — see the toolkit README for the exact command and its output.

For every `BlockInterface` (AE2 block), `fluid_interface` (AE2FC Dual Interface block), and
`BlockCableBus` part of type `interface`/`ae2fc:part_fluid_interface`: neighbours, patterns
(outputs/inputs resolved to localized names via idmaps + recipes.sqlite), inventory/config/
upgrades, current name (if any), and — when the naming rule below is satisfied — a proposed name.

Naming rule (deterministic, conservative; full prose also written into the output .md):
  1. Targets = neighbours that can receive an item/fluid (block interface: up to 6, or 1 if
     `pointAt` is set; cable part: the 1 block it faces). Cable/energy wire, ME Drive/Controller/
     Wireless, an interface of the *same* ME network, and an EnderIO conduit with no external
     connection facing the interface are ignored, not blocking.
  2. A tank/chest/drawer, a GT item/fluid pipe, a *connected* EnderIO conduit, an interface of
     *another* network, another AE2 bus/plane/p2p facing it, or an unidentified block/hatch that
     doesn't accept input blocks naming (`?`) — remote flow is never inferred.
  3. Exactly one target must remain: a GT single-block machine, or one or more hatches/buses of one
     multiblock (matched to its controller by the hatch's own `recipeMap` NBT tag against an
     empirically-built controller→recipe-map table, Chebyshev distance ≤6, no ties) — or one/more
     Molecular Assemblers with only crafting (`crafting=1`) patterns.
  4. Every output of every pattern must be producible by that machine's recipe map(s) in
     `recipes.sqlite` (multi-map machines like Material Press accept any of their maps). Patterns
     whose output item was removed from the pack are skipped, not counted against the interface.
  5. Name = `<Machine> [<map>] — <up to 3 most common outputs> +N (C<circuit>)`, ≤90 chars. Circuit
     comes from a Programmed Circuit in the target Input Bus, else from the patterns themselves.
     An interface that already has a name keeps it (`proposal=null`); the calculated name is still
     recorded in `computed` for comparison. An interface with no patterns gets `?` plus a note of
     what it stores (`config`/`inv`/`storage`), never a guessed name.

Caveat: recipes.sqlite is a NEI dump of one pack version; the world may be on another. A recipe
that changed between versions can produce a false `?` here — always sanity-check `?` results by
eye before concluding a pattern/interface is actually broken.

Inputs (all required unless noted):
  --dump          dump_tes.py output for the dimension (json/json.gz)
  --patterns      scan_patterns.py output, SAME dimension (json/json.gz)
  --idmaps        idmaps.py output; "live" key must match the dump's world (json/json.gz)
  --mid-names     {gt "mID" (int, as string key): "Machine Name"} map (the mid_names.json an
                  earlier scan produced)
  --db            recipes.sqlite (default: ./recipes.sqlite)
  --region-dir    OPTIONAL: the dump's region/ dir, for exact EnderIO conduit connection state.
                  Without it every conduit-adjacent interface is conservatively left unnamed
                  (treated as if connected) rather than guessed — matches this tool's "when
                  ambiguous, leave it alone" policy, at the cost of a few extra `?`.
  --dim-label     label used in output headers, e.g. "DIM7" (default: basename of --dump)
  --generated     date string for output headers (default: today, YYYY-MM-DD)

Outputs:
  --out-json      interfaces_<dim>.json — full per-interface record (neighbours, patterns, inv, ...)
  --out-proposal  interface_names_proposal.json — {idx,te,x,y,z,side,region,chunk,name_key,
                  current_name,computed,proposal,reason} per interface; feed straight into
                  apply_interface_names.py --proposal
  --out-md        interfaces_<dim>.md — human-readable table + the rule prose above
"""
import argparse
import collections
import datetime
import gzip
import json
import os
import re
import sqlite3
import sys


# ----------------------------------------------------------------------------------------------
# I/O helpers
# ----------------------------------------------------------------------------------------------
def load_json(path):
    op = gzip.open if path.endswith(".gz") else open
    with op(path, "rt", encoding="utf-8") as f:
        return json.load(f)


DIRS = {0: (0, -1, 0), 1: (0, 1, 0), 2: (0, 0, -1), 3: (0, 0, 1), 4: (-1, 0, 0), 5: (1, 0, 0)}
DIRN = {0: "down", 1: "up", 2: "north", 3: "south", 4: "west", 5: "east", 6: "none"}
DIR_BY_NAME = {v: k for k, v in DIRN.items()}
OPP = {0: 1, 1: 0, 2: 3, 3: 2, 4: 5, 5: 4}

STORAGE_WORDS = ("Super Tank", "Quantum Tank", "Super Chest", "Quantum Chest", "Digital Tank", "Digital Chest", "Storage")
IGNORE_TE = {"BlockWirelessConnector", "BlockDrive", "BlockController", "BlockCableBus", "BlockInterface",
             "fluid_interface", "BlockCraftingMonitor", "BlockAdvancedCraftingUnit", "BlockSingularityCraftingStorage",
             "BlockDenseEnergyCell", "BlockEnergyCell", "BlockQuartzGrowthAccelerator", "BlockCellWorkbench",
             "BlockCondenser", "BlockInscriber", "BlockSecurity", "BlockQuantumRing", "BlockQuantumLinkChamber",
             "BlockCraftingUnit", "BlockCraftingStorage", "BlockPaint", "BlockCharger", "BlockEnergyAcceptor",
             "BlockLightDetector", "BlockQuartzTorch", "BlockSkyChest", "BlockTinyTNT", "BlockVibrationChamber",
             "BlockSpatialPylon", "BlockSpatialIOPort", "BlockChest", "level_maintainer"}
STORAGE_TE = {"Avaritiaddons:CompressedChest", "Ender Chest", "Chest", "Trap Chest", "blockReservoirTileEntity",
              "net.minecraft.src.buildcraft.factory.TileTank", "com.indemnity83.irontank.block.TileIronTank",
              "StorageDrawers:tileDrawersStandard", "StorageDrawers:tileDrawersComp", "BlockIOPort"}
SINGLE_MAP = {"Circuit Assembler": "Circuit Assembler", "Chemical Reactor": "Chemical Reactor", "Arc Furnace": "Arc Furnace",
              "Fluid Heater": "Fluid Heater", "Fluid Solidifier": "Fluid Solidifier", "Alloy Smelter": "Alloy Smelter",
              "Assembling Machine": "Assembler", "Mixer": "Mixer", "Centrifuge": "Centrifuge", "Electrolyzer": "Electrolyzer",
              "Macerator": "Macerator", "Extruder": "Extruder", "Wiremill": "Wiremill", "Lathe": "Lathe",
              "Bending Machine": "Bending Machine", "Forming Press": "Forming Press", "Compressor": "Compressor",
              "Extractor": "Extractor", "Fluid Extractor": "Fluid Extractor", "Cutting Machine": "Cutting Machine",
              "Forge Hammer": "Forge Hammer", "Laser Engraver": "Laser Engraver", "Autoclave": "Autoclave",
              "Chemical Bath": "Chemical Bath", "Sifter": "Sifter", "Canner": "Canner", "Packager": "Packager",
              "Unpackager": "Unpackager", "Polarizer": "Electromagnetic Polarizer",
              "Electromagnetic Separator": "Electromagnetic Separator", "Thermal Centrifuge": "Thermal Centrifuge",
              "Ore Washer": "Ore Washer", "Fermenter": "Fermenter", "Distillery": "Distillery", "Brewery": "Brewery",
              "Printer": "Printer", "Slicer": "Slicer", "Electric Furnace": "Furnace", "Microwave": "Furnace",
              "Fluid Canner": "Fluid Canner", "Plasma Arc Furnace": "Plasma Arc Furnace", "Recycler": "Recycler"}
SETTINGS = ("BLOCK", "SMART_BLOCK", "INSERTION_MODE", "ADVANCED_BLOCKING_MODE", "LOCK_CRAFTING_MODE",
            "INTERFACE_TERMINAL", "PATTERN_OPTIMIZATION", "FUZZY_MODE", "SIDELESS_MODE", "priority")
BLOCKING = {
    "conduit": "connected EnderIO conduit (remote flow)",
    "storage": "adjacent tank/chest (destination not inferable)",
    "pipe": "adjacent GT pipe (remote flow)",
    "other": "unknown adjacent block",
    "controller": "adjacent controller (not an input)",
    "other_ae2": "AE2 part facing it (bus/plane/p2p)",
    "interface_other_network": "faces an interface of another network (sub-network; final destination not inferable)",
}
IGNORABLE = {"none", "cable", "interface", "wire", "ignorable", "conduit_loose", "hatch_not_input", "interface_same_network"}


def clean_name(s):
    return re.sub(r"§.", "", s) if isinstance(s, str) else s


# ----------------------------------------------------------------------------------------------
# Build
# ----------------------------------------------------------------------------------------------
def build(dump, patterns, idmaps, mid_names, db_path, region_dir=None):
    """`patterns` (scan_patterns.py output, or None) is only used as a cross-check: interface
    patterns are read directly from each TE's embedded `patterns`/`extra:<side>` compound in
    `dump`, exactly like the original pipeline did (nothing else touches `patterns`)."""
    tes = dump["tes"]
    live = idmaps["live"]

    def iname(i):
        return live.get(f"item:{i}") or live.get(f"block:{i}")

    mid = {int(k): v for k, v in mid_names.items()}

    def gtname(t):
        return mid.get(t.get("mID"), f'mID {t.get("mID")}')

    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = db.cursor()
    loc_by_id = dict(cur.execute("select ID,LOCALIZED_NAME from ITEM"))
    db_item_ids = set(r[0] for r in cur.execute("select ID from ITEM"))
    rtypes = cur.execute("select ID,TYPE from RECIPE_TYPE").fetchall()
    rtype = {rid: typ for rid, typ in rtypes}
    rec_type = dict(cur.execute("select ID,RECIPE_TYPE_ID from RECIPE"))
    out_types = collections.defaultdict(set)
    for rid, item in cur.execute("select RECIPE_ID,ITEM_OUTPUTS_VALUE_ITEM_ID from RECIPE_ITEM_OUTPUTS"):
        out_types[item].add(rec_type.get(rid))
    fout_types = collections.defaultdict(set)
    for rid, fl in cur.execute("select RECIPE_ID,FLUID_OUTPUTS_VALUE_FLUID_ID from RECIPE_FLUID_OUTPUTS"):
        fout_types[fl].add(rec_type.get(rid))
    fluid_ids = {}
    for fid, internal, locn in cur.execute("select ID,INTERNAL_NAME,LOCALIZED_NAME from FLUID"):
        fluid_ids[internal] = (fid, locn)
    base_types = collections.defaultdict(set)
    for rid, typ in rtypes:
        base_types[re.sub(r" \(\w+\)$", "", typ)].add(rid)
    rt_by_tail = collections.defaultdict(list)

    def tail(u):
        return u.split(".recipe.", 1)[1] if ".recipe." in u else u

    for rid, typ in rtypes:
        parts = rid.split("~")
        if len(parts) >= 3:
            rt_by_tail[tail(parts[2])].append((rid, typ))
    map2types = {}

    def type_base(rid):
        return re.sub(r" \(\w+\)$", "", rtype.get(rid, "?"))

    def db_item_id(i, dmg):
        reg = iname(i)
        if not reg or ":" not in reg:
            return None
        mod, internal = reg.split(":", 1)
        mod = mod.replace("|", "")
        for d in (dmg, 0):
            k = f"i~{mod}~{internal}~{d}"
            if k in db_item_ids:
                return k
        return None

    def item_name(i, dmg):
        reg = iname(i)
        if reg is None:
            return f"[removed id {i}:{dmg}]"
        k = db_item_id(i, dmg)
        if k:
            return loc_by_id[k]
        if reg == "gregtech:gt.blockmachines":
            return mid.get(dmg, f"gt.blockmachines:{dmg}")
        return f"{reg}:{dmg}"

    def stack_desc(s):
        if not s or "id" not in s:
            return None
        n = s.get("Count", 0) or s.get("Cnt", 1)
        reg = iname(s["id"])
        if reg == "ae2fc:fluid_drop":
            fl = (s.get("tag") or {}).get("Fluid")
            fi_ = fluid_ids.get(fl)
            return {"kind": "fluid", "fluid": fl, "name": (fi_[1] if fi_ else str(fl)) + " (L)", "n": n,
                    "db": fi_[0] if fi_ else None}
        if reg == "ae2fc:fluid_packet":
            fs = (s.get("tag") or {}).get("FluidStack") or {}
            fl = fs.get("FluidName")
            fi_ = fluid_ids.get(fl)
            return {"kind": "fluid", "fluid": fl, "name": (fi_[1] if fi_ else str(fl)) + " (L)",
                    "n": fs.get("Amount", s.get("Cnt", 0)), "db": fi_[0] if fi_ else None}
        return {"kind": "item", "id": s["id"], "dmg": s.get("Damage", 0), "name": item_name(s["id"], s.get("Damage", 0)),
                "n": n, "db": db_item_id(s["id"], s.get("Damage", 0)), "removed": reg is None}

    def pat_summary(pats):
        res = []
        for key in sorted(pats, key=lambda k: int(k.strip("#"))):
            v = pats[key]
            if not v or "tag" not in v:
                continue
            tg = v["tag"]
            outs = [stack_desc(s) for s in tg.get("out", [])]
            outs = [o for o in outs if o]
            if not outs:
                outs = [o for o in (stack_desc(s) for s in tg.get("Outputs", [])) if o]
            ins = [o for o in (stack_desc(s) for s in tg.get("in", [])) if o]
            res.append({"slot": int(key.strip("#")), "crafting": int(tg.get("crafting", 0)),
                        "fluid_pattern": "Inputs" in tg, "invalid_flag": "InvalidPattern" in tg, "out": outs, "in": ins})
        return res

    # -- GT machines / controllers -----------------------------------------------------------
    gt = [t for t in tes if t["id"] == "BaseMetaTileEntity"]

    def is_ctrl(t):
        return t["id"] == "BaseMetaTileEntity" and "mRuntime" in t

    controllers = [t for t in gt if is_ctrl(t)]

    def near_controller(x, y, z, r=8):
        best = []
        for c in controllers:
            d = max(abs(c["x"] - x), abs(c["y"] - y), abs(c["z"] - z))
            if d <= r:
                best.append((d, c))
        best.sort(key=lambda p: p[0])
        return [{"name": gtname(c), "pos": [c["x"], c["y"], c["z"]], "dist": d} for d, c in best[:3]]

    h = [t for t in gt if "Bus" in gtname(t) or "Hatch" in gtname(t)]

    def hatch_types(t):
        m = t.get("recipeMap")
        lst = (map2types.get(m) or rt_by_tail.get(tail(m), [])) if m else []
        return m, set(rid for rid, _ in lst)

    ctrl_maps_raw = collections.defaultdict(collections.Counter)
    for t in h:
        m = t.get("recipeMap")
        if not m:
            continue
        g = near_controller(t["x"], t["y"], t["z"], r=6)
        if not g:
            continue
        if len(g) > 1 and g[1]["dist"] == g[0]["dist"] and g[1]["name"] != g[0]["name"]:
            continue
        ctrl_maps_raw[g[0]["name"]][m] += 1
    ctrl_maps = {}
    for k, c in ctrl_maps_raw.items():
        tot = sum(c.values())
        ctrl_maps[k] = {m: n for m, n in c.items() if n >= 0.1 * tot}

    def pick_controller(c):
        cg = c["controller_guess"]
        rmap = c["recipe_map"]
        cons = [g for g in cg if rmap in ctrl_maps.get(g["name"], {}) and g["dist"] <= 6]
        if not cons:
            return None, None, f"no controller within ≤6 blocks whose map matches recipeMap={rmap}"
        d0 = cons[0]["dist"]
        names = set(g["name"] for g in cons if g["dist"] == d0)
        if len(names) > 1:
            return None, None, f"controllers tied at {d0} blocks ({' / '.join(sorted(names))})"
        return cons[0]["name"], d0, None

    def single_block_types(name):
        n = re.sub(r"^(Advanced|Basic|Elite|Ultimate|Turbo|Simple|Precise)\s+", "", name)
        n = re.sub(r"\s+(I|II|III|IV|V|VI)$", "", n).strip()
        b = SINGLE_MAP.get(n) or (n if n in base_types else None)
        return (b, base_types[b]) if b else (None, set())

    posmap = collections.defaultdict(list)
    for t in tes:
        if "x" in t:
            posmap[(t["x"], t["y"], t["z"])].append(t)

    def part_type(d):
        reg = iname(d["id"])
        if reg == "appliedenergistics2:item.ItemMultiPart":
            return {440: "interface", 220: "storage_bus", 260: "export_bus", 240: "import_bus", 460: "p2p",
                    120: "cable_anchor", 140: "quartz_fiber", 300: "annihilation_plane", 280: "level_emitter",
                    360: "?360", 380: "terminal?"}.get(d["Damage"], f'multipart:{d["Damage"]}')
        return reg

    def describe_te(t):
        if t is None:
            return None
        i = t["id"]
        if i == "BaseMetaTileEntity":
            return {"te": i, "mID": t.get("mID"), "name": gtname(t), "controller": is_ctrl(t)}
        if i == "BaseMetaPipeEntity":
            return {"te": i, "mID": t.get("mID"), "name": gtname(t)}
        if i == "BlockCableBus":
            parts = {s: part_type(t[f"def:{s}"]) for s in range(7) if f"def:{s}" in t}
            return {"te": i, "parts": parts}
        return {"te": i}

    def neighbour(x, y, z):
        ts = posmap.get((x, y, z))
        t = ts[0] if ts else None
        d = describe_te(t)
        if d and d.get("te") == "BaseMetaTileEntity" and not d["controller"] and any(w in d["name"] for w in ("Hatch", "Bus")):
            d["controller_guess"] = near_controller(x, y, z)
        return {"pos": [x, y, z], **({} if d is None else d)} if d else {"pos": [x, y, z], "te": None}

    # -- enumerate interfaces -----------------------------------------------------------------
    ifaces = []
    for t in tes:
        if t["id"] in ("BlockInterface", "fluid_interface"):
            ifaces.append({"te": t["id"], "x": t["x"], "y": t["y"], "z": t["z"], "side": None, "_t": t,
                            "_pat": t.get("patterns", {})})
        elif t["id"] == "BlockCableBus":
            for s in range(6):
                d = t.get(f"def:{s}")
                if d and part_type(d) in ("interface", "ae2fc:part_fluid_interface"):
                    ifaces.append({"te": t["id"], "x": t["x"], "y": t["y"], "z": t["z"], "side": s, "part": part_type(d),
                                    "_t": t, "_pat": t.get(f"extra:{s}", {}).get("patterns", {}), "_def": d,
                                    "_extra": t.get(f"extra:{s}", {})})

    for i in ifaces:
        i["patterns"] = pat_summary(i["_pat"])
        i["grid"] = (i["_t"].get("proxy") or {}).get("g") if i["side"] is None else (i["_extra"].get("part") or {}).get("g")
        i["current_name"] = i["_t"].get("customName") if i["side"] is None else ((i["_def"].get("tag") or {}).get("display") or {}).get("Name")
    iface_by_pos_side = {(i["x"], i["y"], i["z"], i["side"]): i for i in ifaces}

    if patterns is not None:
        dump_pat_count = sum(len(i["patterns"]) for i in ifaces)
        scan_pat_count = sum(1 for p in patterns if p["te"] in ("BlockInterface", "fluid_interface", "BlockCableBus"))
        if dump_pat_count != scan_pat_count:
            print(f"WARN: --dump has {dump_pat_count} interface patterns embedded but --patterns counts "
                  f"{scan_pat_count} for the same TE types — they were likely taken at different times", file=sys.stderr)

    # -- neighbours -----------------------------------------------------------------------------
    for i in ifaces:
        x, y, z = i["x"], i["y"], i["z"]
        if i["side"] is None:
            i["neighbours"] = {DIRN[s]: neighbour(x + dx, y + dy, z + dz) for s, (dx, dy, dz) in DIRS.items()}
            i["point_at"] = i["_t"].get("pointAt")
        else:
            dx, dy, dz = DIRS[i["side"]]
            i["neighbours"] = {"front": neighbour(x + dx, y + dy, z + dz)}

    # -- EnderIO conduit connection state (needs raw region access) -----------------------------
    ext = {}
    if region_dir:
        import io as _io
        import struct as _struct
        import zlib as _zlib
        import nbtlib as _nbtlib
        want = {}
        for i in ifaces:
            for d, nb in i["neighbours"].items():
                if nb.get("te") == "blockConduitBundleTileEntity":
                    t = posmap[tuple(nb["pos"])][0]
                    want[tuple(nb["pos"])] = (t["_region"], t["_chunk"])
        bychunk = collections.defaultdict(set)
        for pos, (r, c) in want.items():
            bychunk[(r, c)].add(pos)

        def read_chunk(region_file, idx):
            data = open(region_file, "rb").read()
            off = _struct.unpack(">I", b"\0" + data[idx * 4:idx * 4 + 3])[0]
            p = off * 4096
            ln = _struct.unpack(">I", data[p:p + 4])[0]
            ct = data[p + 4]
            raw = data[p + 5:p + 4 + ln]
            blob = _zlib.decompress(raw) if ct == 2 else gzip.decompress(raw)
            return _nbtlib.File.parse(_io.BytesIO(blob))

        for (r, c), poss in bychunk.items():
            root = read_chunk(os.path.join(region_dir, r), c)
            lvl = root.get("Level") or root[""]["Level"]
            for t in lvl["TileEntities"]:
                pos = (int(t["x"]), int(t["y"]), int(t["z"]))
                if pos in poss:
                    ext[pos] = [(str(cc["conduitType"]).split(".")[-1], [int(v) for v in cc["conduit"].get("externalConnections", [])])
                                for cc in t["conduits"]]

    def conduit_kind(ipos, dirname, nbpos):
        s = DIR_BY_NAME[dirname] if dirname != "front" else None
        if s is None:
            s = [k for k, (dx, dy, dz) in DIRS.items() if (ipos[0] + dx, ipos[1] + dy, ipos[2] + dz) == tuple(nbpos)][0]
        back = OPP[s]
        conns = ext.get(tuple(nbpos), [])
        linked = [ct for ct, dirs in conns if back in dirs]
        if linked:
            return {"kind": "conduit", "machine": "EnderIO " + "+".join(linked)}
        if conns and region_dir:
            return {"kind": "conduit_loose", "machine": "EnderIO " + "+".join(ct for ct, _ in conns)}
        # no --region-dir (or bundle has no conduits at all): can't prove it's disconnected -> block conservatively
        return {"kind": "conduit", "machine": "EnderIO (connection unknown without --region-dir)"}

    def classify_base(nb):
        te = nb.get("te")
        if te is None:
            return {"kind": "none"}
        if te == "BlockCableBus":
            return {"kind": "cable", "detail": nb.get("parts")}
        if te in ("BlockInterface", "fluid_interface"):
            return {"kind": "interface"}
        if te == "BlockMolecularAssembler":
            return {"kind": "assembler", "machine": "Molecular Assembler", "types": base_types["Crafting"], "base": "Crafting"}
        if te == "Avaritiaddons:ExtremeAutoCrafter":
            return {"kind": "machine", "machine": "Extreme Auto Crafter", "types": base_types["Extreme Crafting"], "base": "Extreme Crafting"}
        if te == "BaseMetaPipeEntity":
            name = nb.get("name") or ""
            if "Wire" in name or "Cable" in name:
                return {"kind": "wire", "machine": name}
            return {"kind": "pipe", "machine": name}
        if te == "BaseMetaTileEntity":
            name = nb["name"]
            if nb["controller"]:
                return {"kind": "controller", "machine": name}
            if any(w in name for w in STORAGE_WORDS):
                return {"kind": "storage", "machine": name}
            if "Hatch" in name or "Bus" in name:
                t = posmap[tuple(nb["pos"])][0]
                m, types = hatch_types(t)
                d = {"kind": "hatch", "machine": name, "recipe_map": m, "types": types, "controller_guess": nb.get("controller_guess", [])}
                clean = re.sub(r"§.", "", name)
                if not ("Input" in clean or "Super Bus (I)" in clean or "Solidifier Hatch" in clean):
                    d["kind"] = "hatch_not_input"
                return d
            b, types = single_block_types(name)
            return {"kind": "machine", "machine": name, "types": types, "base": b}
        if te in IGNORE_TE:
            return {"kind": "ignorable", "machine": te}
        if te in STORAGE_TE:
            return {"kind": "storage", "machine": te}
        return {"kind": "other", "machine": te}

    def facing_side(ipos, nbpos):
        for k, (dx, dy, dz) in DIRS.items():
            if (ipos[0] + dx, ipos[1] + dy, ipos[2] + dz) == tuple(nbpos):
                return k
        return None

    def classify(i, d, nb):
        if nb.get("te") == "blockConduitBundleTileEntity":
            return conduit_kind((i["x"], i["y"], i["z"]), d, nb["pos"])
        if nb.get("te") == "BlockCableBus":
            back = OPP[facing_side((i["x"], i["y"], i["z"]), nb["pos"])]
            p = (nb.get("parts") or {}).get(back)
            if p in ("interface", "ae2fc:part_fluid_interface"):
                other = iface_by_pos_side.get((*nb["pos"], back))
                og = other["grid"] if other else None
                if og is not None and og == i["grid"]:
                    return {"kind": "interface_same_network", "machine": "interface on the same network (ignored)"}
                return {"kind": "interface_other_network", "machine": f"interface of ANOTHER network (grid {og}) — sub-network"}
            if p and p not in ("cable_anchor", "quartz_fiber"):
                return {"kind": "other_ae2", "machine": f"AE2 part {p} facing it"}
            return {"kind": "cable"}
        c = classify_base(nb)
        if c["kind"] == "assembler":
            c["types"] = base_types["Crafting"]
        if c.get("base") == "Extreme Crafting":
            c["types"] = base_types["Extreme Crafting"]
        return c

    for i in ifaces:
        tg = i["neighbours"]
        if i["side"] is None and i.get("point_at") in DIRS:
            tg = {DIRN[i["point_at"]]: tg[DIRN[i["point_at"]]]}
        i["targets"] = {d: classify(i, d, nb) for d, nb in tg.items()}

    # -- output-matching / naming evaluation -----------------------------------------------------
    def outputs_ok(pats, types, crafting_only):
        probs, skipped, okc = [], [], 0
        for p in pats:
            if crafting_only is not None and bool(p["crafting"]) != crafting_only:
                probs.append(f"slot {p['slot']}: {'crafting' if p['crafting'] else 'processing'} pattern in a {'crafting' if crafting_only else 'machine'} interface")
                continue
            if any(o["kind"] == "item" and o.get("removed") for o in p["out"]):
                skipped.append(f"slot {p['slot']}: removed item (already listed as broken)")
                continue
            good = True
            for o in p["out"]:
                if o["kind"] == "fluid":
                    prod = fout_types.get(o["db"], set()) if o["db"] else set()
                else:
                    if not o["db"]:
                        probs.append(f"slot {p['slot']}: output {o['name']} is not in the recipe dump (old ID?)")
                        good = False
                        continue
                    prod = out_types.get(o["db"], set())
                if not prod & types:
                    probs.append(f"slot {p['slot']}: {o['name']} is not produced by this map (it is: {', '.join(sorted(set(type_base(r) for r in prod))[:4]) or 'no recipe'})")
                    good = False
            okc += good
        if okc == 0 and skipped and not probs:
            probs.append("every pattern has a removed item")
        return (not probs), probs, skipped

    def cfg_items(i):
        src = i["_t"] if i["side"] is None else i["_extra"]
        res = []
        for key in ("config", "ConfigInv"):
            for k, v in sorted((src.get(key) or {}).items()):
                d = stack_desc(v) if v else None
                if d:
                    res.append(d["name"])
        return res

    def evaluate(i):
        tg = {d: c for d, c in i["targets"].items() if c["kind"] not in IGNORABLE}
        ev = {}
        if not i["patterns"]:
            return None, "no patterns" + (" (stock interface: config=" + ", ".join(i["config_items"][:3]) + ")" if i.get("config_items") else ""), None, [], ev
        blocks = [f"{d}: {BLOCKING[c['kind']]} [{c.get('machine')}]" for d, c in tg.items() if c["kind"] in BLOCKING]
        if blocks:
            return None, "; ".join(blocks), None, [], ev
        if not tg:
            return None, "no neighbour that can receive items (remote flow? e.g. pulled by a bus/conduit somewhere else)", None, [], ev
        machs = list(tg.values())
        kinds = set(c["kind"] for c in machs)
        if kinds == {"assembler"}:
            ev["assemblers"] = len(machs)
            nonc = [p["slot"] for p in i["patterns"] if not p["crafting"]]
            if nonc:
                return None, f"Molecular Assembler but processing patterns in slots {nonc[:5]}", "Crafting", [], ev
            ok, probs, sk = outputs_ok(i["patterns"], machs[0]["types"], True)
            ev["db_mismatch"] = probs
            return "Crafting", None, "Crafting", sk, ev
        if len(machs) > 1:
            hs = [c for c in machs if c["kind"] == "hatch"]
            if len(hs) == len(machs):
                picks = set(pick_controller(c)[:2] for c in hs)
                if len(picks) == 1 and None not in list(picks)[0]:
                    machs = [hs[0]]
                    ev["hatches"] = len(hs)
                else:
                    return None, "more than one adjacent machine: " + ", ".join(f"{d}={re.sub(r'§.', '', c['machine'])}" for d, c in tg.items()), None, [], ev
            else:
                return None, "more than one adjacent machine: " + ", ".join(f"{d}={re.sub(r'§.', '', c['machine'])}" for d, c in tg.items()), None, [], ev
        c = machs[0]
        if c["kind"] == "hatch":
            hn = re.sub(r"§.", "", c["machine"])
            if not c["recipe_map"]:
                return None, f"{hn} without recipeMap (multiblock never formed?)", None, [], ev
            name, dist, note = pick_controller(c)
            if not name:
                return None, f"{hn}: {note}", None, [], ev
            machine = name
            ev["hatch"] = hn
            ev["controller_dist"] = dist
            ev["recipe_map"] = c["recipe_map"]
            maps = list(ctrl_maps[name])
            ev["maps"] = maps
            types = set()
            for m in maps:
                types |= set(rid for rid, _ in (map2types.get(m) or rt_by_tail.get(tail(m), [])))
            if not types:
                return None, f"{machine}: map {maps} does not exist in the recipe dump", None, [], ev
        else:
            machine = c["machine"]
            types = c["types"]
            ev["single_block"] = machine
            if not types:
                return None, f"{machine}: do not know which recipe map to use", None, [], ev
        ok, probs, sk = outputs_ok(i["patterns"], types, False)
        if not ok:
            return None, f"{machine}: " + "; ".join(probs[:3]) + (f" (+{len(probs) - 3})" if len(probs) > 3 else ""), machine, sk, ev
        return machine, None, machine, sk, ev

    def top_outputs(pats, k=3):
        cnt = collections.Counter()
        for p in pats:
            for o in p["out"][:1]:
                nm = re.sub(r"\s*\(L\)$", "", o["name"])
                cnt[nm] += 1
        return [n for n, _ in cnt.most_common(k)], len(cnt)

    def pattern_circuit(p):
        return set(s["dmg"] for s in p["in"] if s["kind"] == "item" and iname(s["id"]) == "gregtech:gt.integrated_circuit")

    def hatch_circuit(pos):
        t = posmap.get(tuple(pos))
        if not t:
            return set()
        return set(int(s.get("Damage", 0)) for s in t[0].get("Inventory", [])
                   if isinstance(s, dict) and iname(s.get("id")) == "gregtech:gt.integrated_circuit")

    def circuit_for(i):
        cs = set()
        for d, c in i["targets"].items():
            if c["kind"] == "hatch":
                tgn = i["neighbours"][d]
                cs |= hatch_circuit(tgn["pos"])
        pc = [pattern_circuit(p) for p in i["patterns"]]
        if not cs and pc and all(len(c) == 1 for c in pc) and len(set().union(*pc)) == 1:
            cs = set().union(*pc)
        return cs

    def compose(machine, pats, circ, maxlen=90):
        outs, total = top_outputs(pats)
        suffix = f" (C{sorted(circ)[0]})" if len(circ) == 1 else (f" (C{'&'.join(str(c) for c in sorted(circ))})" if circ else "")
        for k in (3, 2, 1):
            s = f"{machine} — {', '.join(outs[:k])}" + (f" +{total - k}" if total > k else "") + suffix
            if len(s) <= maxlen or k == 1:
                return s
        return None

    def produced_by_map(pats, m):
        types = set(rid for rid, _ in (map2types.get(m) or rt_by_tail.get(tail(m), [])))
        for p in pats:
            if any(o["kind"] == "item" and o.get("removed") for o in p["out"]):
                continue
            for o in p["out"]:
                prod = (fout_types.get(o["db"], set()) if o["kind"] == "fluid" else out_types.get(o["db"], set())) if o["db"] else set()
                if not prod & types:
                    return False
        return True

    for i in ifaces:
        for p in i["patterns"]:
            for o in p["out"] + p["in"]:
                o["name"] = clean_name(o["name"])
        i["config_items"] = cfg_items(i)
        m, reason, mach, sk, ev = evaluate(i)
        i["machine"] = mach
        i["reason"] = reason
        i["skipped"] = sk
        i["evidence"] = ev
        i["circuit"] = sorted(circuit_for(i))
        i["machine_label"] = mach
        if mach and not reason and len(ev.get("maps", [])) > 1:
            ok = [mm for mm in ev["maps"] if produced_by_map(i["patterns"], mm)]
            if len(ok) == 1:
                nm = type_base((map2types.get(ok[0]) or rt_by_tail.get(tail(ok[0])))[0][0])
                i["machine_label"] = f"{mach} [{nm}]"
                ev["map_used"] = ok[0]
        i["computed"] = compose(i["machine_label"], i["patterns"], i["circuit"]) if mach and not reason else None
        if i["current_name"] and i["computed"]:
            i["proposal"] = None
            i["reason"] = f"already named ('{i['current_name']}'); the computed name matches the machine — kept"
        else:
            i["proposal"] = i["computed"]

    def inv_items(src, key):
        res = []
        for k, v in sorted((src.get(key) or {}).items()):
            d = stack_desc(v) if v else None
            if d:
                res.append(f"{d['n']}x {clean_name(d['name'])}")
        return res

    def fluid_inv(src):
        res = []
        for k, v in sorted((src.get("FluidInv") or {}).items()):
            if v and "FluidName" in v:
                res.append(f"{v.get('Amount', 0)} mB {v['FluidName']}")
        return res

    def category(r):
        if r["proposal"]:
            return "name proposed"
        rs = r["reason"] or ""
        if rs.startswith("already named"):
            return "already named (computed matches; kept)"
        if rs.startswith("no patterns (empty)"):
            return "no patterns, empty"
        if rs.startswith("no patterns"):
            return "no patterns (stock/inventory)"
        if "tank/chest" in rs:
            return "adjacent tank/chest"
        if "conduit" in rs:
            return "connected EnderIO conduit"
        if "sub-network" in rs:
            return "faces an interface of another network (sub-network)"
        if "AE2 part facing it" in rs:
            return "storage/export bus of another network facing it"
        if "GT pipe" in rs:
            return "adjacent GT pipe"
        if rs.startswith("no neighbour"):
            return "no neighbour that can receive items"
        if rs.startswith("more than one adjacent machine"):
            return "more than one adjacent machine"
        if "unknown adjacent block" in rs:
            return "unknown adjacent block"
        if "is not produced" in rs or "is not in the recipe dump" in rs or "removed item" in rs:
            return "output does not match the machine's map"
        if "controller" in rs or "recipeMap" in rs:
            return "hatch with no identifiable controller"
        return "other"

    def build_records():
        recs = []
        for idx, i in enumerate(ifaces):
            src = i["_t"] if i["side"] is None else i["_extra"]
            t = i["_t"]
            outs, total = top_outputs(i["patterns"], k=6)
            r = {"idx": idx, "te": i["te"], "x": i["x"], "y": i["y"], "z": i["z"], "side": i["side"],
                 "side_name": DIRN[i["side"]] if i["side"] is not None else None,
                 "kind": {"BlockInterface": "ME Interface (block)", "fluid_interface": "Dual Interface (block, AE2FC)"}.get(i["te"])
                 or ("Dual Interface (part)" if i.get("part") == "ae2fc:part_fluid_interface" else "ME Interface (part)"),
                 "part_item": (iname(i["_def"]["id"]) + ":" + str(i["_def"]["Damage"])) if i["side"] is not None else None,
                 "region": t["_region"], "chunk": t["_chunk"], "grid": i["grid"],
                 "name_key": "customName" if i["side"] is None else f"def:{i['side']}/tag/display/Name",
                 "current_name": i["current_name"],
                 "point_at": (DIRN.get(i.get("point_at")) if i["side"] is None else None),
                 "settings": {k: src.get(k) for k in SETTINGS if k in src},
                 "upgrades": inv_items(src, "upgrades"),
                 "inv": inv_items(src, "inv") if i["side"] is None else [],
                 "storage": inv_items(src, "storage"),
                 "config": i["config_items"], "fluid_inv": fluid_inv(src),
                 "patterns": {"count": len(i["patterns"]), "crafting": sum(p["crafting"] for p in i["patterns"]),
                              "processing": sum(1 - p["crafting"] for p in i["patterns"]),
                              "fluid_patterns": sum(p["fluid_pattern"] for p in i["patterns"]),
                              "invalid_flag": sum(p["invalid_flag"] for p in i["patterns"]),
                              "distinct_outputs": total, "top_outputs": outs,
                              "list": [{"slot": p["slot"], "crafting": p["crafting"],
                                        "out": [f"{o['n']}x {o['name']}" for o in p["out"]],
                                        "in": [f"{o['n']}x {o['name']}" for o in p["in"]]} for p in i["patterns"]]},
                 "circuit": i["circuit"],
                 "neighbours": {d: {**{k: v for k, v in nb.items() if k != "parts"},
                                     "kind": i["targets"].get(d, {}).get("kind", "ignored (pointAt)"),
                                     "detail": i["targets"].get(d, {}).get("machine"), "parts": nb.get("parts")}
                                for d, nb in i["neighbours"].items()},
                 "machine": i["machine_label"] if i["machine"] else None,
                 "computed": i["computed"], "proposal": i["proposal"], "reason": i["reason"],
                 "evidence": dict(i["evidence"]), "skipped_patterns": i["skipped"]}
            if r["patterns"]["count"] == 0:
                r["reason"] = "no patterns" + (
                    " (stock interface: config=" + ", ".join(r["config"][:4]) + ("…" if len(r["config"]) > 4 else "") + ")"
                    if r["config"] else (" (empty)" if not r["inv"] and not r["storage"] else " (inventory only: " + ", ".join((r["inv"] + r["storage"])[:3]) + ")"))
            r["category"] = category(r)
            recs.append(r)
        recs.sort(key=lambda r: (r["x"], r["y"], r["z"], r["side"] if r["side"] is not None else -1))
        return recs

    records = build_records()
    cats = collections.Counter(r["category"] for r in records)

    # control group: agreement between existing player-given names and the computed name
    KEY = {"Laser Engraver": "Laser Engraver", "Bending": "Bending Machine", "Forming Press": "Forming Press",
           "Mixer": "Mixing Machine", "Wire": "Wire Factory", "Extruder": "Extrusion Machine",
           "Smelter HSS": "Multi Smelter", "Zyngen": "Zyngen", "Alloy Blast Smelter": "Alloy Blast Smelter",
           "Fluid Shaper": "Fluid Shaper", "TurboCan": "TurboCan", "Sledgehammer": "Sledgehammer", "Lathe": "Lathe",
           "Implosion": "Implosion", "Fluid Extractor": "Fluid Extractor", "Cutting Factory": "Cutting Factory",
           "Magnetic Flux": "Magnetic Flux", "Polarizer": "Polarizer"}
    agree, disagree, circ_ok, circ_n = 0, [], 0, 0
    for r in records:
        if r["current_name"] and r["computed"]:
            cur, comp = r["current_name"], r["computed"]
            m = [v for k, v in KEY.items() if k in cur]
            if m and all(v in comp for v in m):
                agree += 1
            else:
                disagree.append((cur, comp))
            cm = re.findall(r"C(\d+)", cur)
            if cm:
                circ_n += 1
                circ_ok += int(set(int(c) for c in cm) == set(r["circuit"]) or (cm == ["0"] and not r["circuit"]))

    control = {"agree": agree, "total_named_and_computed": sum(1 for r in records if r["current_name"] and r["computed"]),
               "disagree": disagree, "circuit_ok": circ_ok, "circuit_total": circ_n}
    return records, cats, control


# ----------------------------------------------------------------------------------------------
# Markdown rendering
# ----------------------------------------------------------------------------------------------
def esc(s):
    return str(s).replace("|", "\\|")


def render_md(records, cats, control, dim_label, generated, source_dump):
    def nb_cell(r):
        parts = []
        for d, nb in r["neighbours"].items():
            te = nb.get("te")
            if te is None:
                continue
            if te == "BaseMetaTileEntity":
                s = clean_name(nb["name"])
                cg = nb.get("controller_guess")
                if cg and not nb["controller"]:
                    s += f" →{cg[0]['name']}@{cg[0]['dist']}"
            elif te == "BlockCableBus":
                s = {"interface_same_network": "interface (same network)", "interface_other_network": "interface of ANOTHER network",
                     "other_ae2": nb.get("detail")}.get(nb["kind"], "AE2 cable")
            elif te == "BaseMetaPipeEntity":
                s = clean_name(nb.get("name") or "pipe")
            elif te == "blockConduitBundleTileEntity":
                s = "EnderIO conduit" + (" (connected)" if nb["kind"] == "conduit" else " (loose)")
            elif te in ("BlockInterface", "fluid_interface"):
                s = "interface (same network)" if nb["kind"] == "interface_same_network" else "interface of ANOTHER network"
            elif te == "BlockMolecularAssembler":
                s = "Molecular Assembler"
            else:
                s = te
            parts.append(f"{d}: {s}")
        return "; ".join(parts) or "—"

    def pat_cell(r):
        p = r["patterns"]
        if not p["count"]:
            return "no patterns" + (f"; config: {', '.join(r['config'][:3])}" if r["config"] else "")
        kind = "crafting" if p["crafting"] == p["count"] else ("proc." if p["processing"] == p["count"] else f"{p['crafting']} craft + {p['processing']} proc.")
        return f"{p['count']} ({kind}): {', '.join(p['top_outputs'][:3])}" + (f" +{p['distinct_outputs'] - 3}" if p["distinct_outputs"] > 3 else "")

    def evid(r):
        ev = r["evidence"]
        if r["computed"]:
            if "hatch" in ev:
                return (f"{ev['hatch']} with recipeMap {ev['recipe_map']}, controller {r['machine'].split(' [')[0]} at {ev['controller_dist']} block(s); "
                        f"every output matches the map" + (f"; {len(r['skipped_patterns'])} pattern(s) with a removed item ignored" if r["skipped_patterns"] else ""))
            if "single_block" in ev:
                return f"adjacent single-block machine {ev['single_block']}; every output matches the map"
            if "assemblers" in ev:
                return f"{ev['assemblers']} adjacent Molecular Assembler(s); crafting patterns only (crafting=1)" + \
                    (f"; {len(ev.get('db_mismatch', []))} output(s) not found in the recipe dump" if ev.get("db_mismatch") else "")
        return r["reason"] or ""

    counts = {"block": sum(1 for r in records if r["kind"] == "ME Interface (block)"),
              "part": sum(1 for r in records if r["kind"] == "ME Interface (part)"),
              "dual_block": sum(1 for r in records if r["kind"] == "Dual Interface (block, AE2FC)"),
              "dual_part": sum(1 for r in records if r["kind"] == "Dual Interface (part)")}
    named = cats["name proposed"]
    kept = cats["already named (computed matches; kept)"]

    lines = []
    L = lines.append
    L(f"# ME Interfaces in dim {dim_label} — inventory and name proposal ({generated})")
    L("")
    L(f"Source: `{source_dump}`. Full per-interface data in `interfaces_{dim_label}.json`; applicable proposal in "
      f"`interface_names_proposal.json`; tool `apply_interface_names.py` (dry-run by default). Generated by "
      f"`build_interfaces.py`.")
    L("")
    L(f"**Totals:** {len(records)} interfaces = {counts['block']} ME Interface (block) + {counts['part']} ME Interface "
      f"(part on a cable) + {counts['dual_block']} Dual Interface (block, AE2FC) + {counts['dual_part']} Dual Interface (part). "
      f"Named today: {sum(1 for r in records if r['current_name'])}. **Name proposed (new): {named}**. Already named and the "
      f"computed name matches (kept): {kept}. No proposal (`?`): {len(records) - named - kept}.")
    L("")
    L("Reasons for `?` (count):")
    for k, v in cats.most_common():
        if k not in ("name proposed", "already named (computed matches; kept)"):
            L(f"- {v} — {k}")
    L("")
    L("## How names are stored (empirical evidence + AE2 rv3-beta-1050-GTNH bytecode)")
    L("- **ME Interface (block, TE `BlockInterface`)**: `customName` (String) key at the TE root. Bytecode: "
      "`appeng/tile/AEBaseTile.readFromNBT/writeToNBT` reads/writes `customName`.")
    L("- **Dual Interface (block, TE `fluid_interface`, AE2FC)**: same `customName` key at the root (inherited from `AEBaseTile`).")
    L("- **Interface as a cable part (TE `BlockCableBus`)** — AE2 (`appliedenergistics2:item.ItemMultiPart` dmg 440) "
      "or AE2FC's Dual (`ae2fc:part_fluid_interface`): the name is the display name of the part's ItemStack, written to "
      "`def:<side>/tag/display/Name`; `extra:<side>` does NOT have a name key. Bytecode: `appeng/parts/AEBasePart."
      "getCustomName()` = `is.getDisplayName()`. Sides: 0=down 1=up 2=north 3=south 4=west 5=east.")
    L("")
    L("## Naming rule (deterministic, conservative) — see the build_interfaces.py docstring for the full prose")
    L(f"**Control group:** of the {control['total_named_and_computed']} interfaces that already had a player-given name "
      f"AND got a computed name, the computed machine matches the current name in {control['agree']}; of the "
      f"{control['circuit_total']} with a circuit number in the name, the circuit read matches in {control['circuit_ok']}.")
    L("")
    L("## Table (sorted by x, y, z)")
    L("")
    L("| # | x | y | z | type / side | current name | neighbours | patterns | proposal | evidence |")
    L("|---|---|---|---|---|---|---|---|---|---|")
    for r in records:
        typ = r["kind"] + (f" / {r['side_name']}" if r["side_name"] else "") + (f" (pointAt {r['point_at']})" if r["point_at"] and r["point_at"] != "none" else "")
        L(f"| {r['idx']} | {r['x']} | {r['y']} | {r['z']} | {esc(typ)} | {esc(r['current_name'] or '')} | {esc(nb_cell(r))} | "
          f"{esc(pat_cell(r))} | {esc(r['proposal'] or ('? (calc: ' + r['computed'] + ')' if r['computed'] else '?'))} | {esc(evid(r))} |")
    L("")
    L("## Caveats")
    L("- The recipe dump may be from a different pack version than the world. Divergences become `?`.")
    L("- `controller_guess` is the nearest controller by Chebyshev distance; touching multiblocks of the same type "
      "can swap controllers with each other (same name, so the proposed name does not change).")
    L("- Stock interfaces (only `config`, no patterns) get no name from the rule — the reason lists what they stock.")
    L("- `#` is the index in `interfaces_<dim>.json`/`interface_names_proposal.json` (identity = `(te, x, y, z, side)`).")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dump", required=True)
    ap.add_argument("--patterns", default=None,
                     help="optional: scan_patterns.py output for the same dim, cross-checked against the dump's own "
                          "embedded pattern count (patterns live per-TE inside --dump; this is a drift check, not a data source)")
    ap.add_argument("--idmaps", required=True)
    ap.add_argument("--mid-names", required=True)
    ap.add_argument("--db", default="recipes.sqlite")
    ap.add_argument("--region-dir", default=None, help="optional: resolve EnderIO conduit connection state exactly")
    ap.add_argument("--dim-label", default=None)
    ap.add_argument("--generated", default=None)
    ap.add_argument("--out-json", default="interfaces.json")
    ap.add_argument("--out-proposal", default="interface_names_proposal.json")
    ap.add_argument("--out-md", default="interfaces.md")
    args = ap.parse_args()

    dump = load_json(args.dump)
    patterns = load_json(args.patterns) if args.patterns else None
    idmaps = load_json(args.idmaps)
    mid_names = load_json(args.mid_names)
    dim_label = args.dim_label or os.path.basename(args.dump).split(".")[0]
    generated = args.generated or datetime.date.today().isoformat()

    records, cats, control = build(dump, patterns, idmaps, mid_names, args.db, args.region_dir)
    print(collections.Counter(bool(r["proposal"]) for r in records))
    print("categories:", dict(cats))
    print("control group:", control)

    json.dump({"generated": generated, "source_dump": os.path.basename(args.dump), "total": len(records),
               "categories": dict(cats), "interfaces": records}, open(args.out_json, "w"), ensure_ascii=False, indent=1)

    prop = [{"idx": r["idx"], "te": r["te"], "x": r["x"], "y": r["y"], "z": r["z"], "side": r["side"],
             "region": r["region"], "chunk": r["chunk"], "name_key": r["name_key"], "current_name": r["current_name"],
             "computed": r["computed"], "proposal": r["proposal"], "reason": r["reason"]} for r in records]
    json.dump({"generated": generated, "dim": dim_label,
               "note": "proposal=null is never written; to rename an already named interface copy computed -> proposal by hand",
               "name_keys": {"BlockInterface": "customName (String at the TE root)", "fluid_interface": "customName (String at the TE root)",
                             "BlockCableBus": "def:<side>/tag/display/Name"},
               "entries": prop}, open(args.out_proposal, "w"), ensure_ascii=False, indent=1)

    md = render_md(records, cats, control, dim_label, generated, os.path.basename(args.dump))
    open(args.out_md, "w", encoding="utf-8").write(md)
    print("wrote", args.out_json, args.out_proposal, args.out_md)


if __name__ == "__main__":
    main()
