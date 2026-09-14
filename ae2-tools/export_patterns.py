#!/usr/bin/env python3
"""Export every AE2 encoded pattern of a scan as **string identities** — "my world's recipes as
strings", the human-diffable artifact behind every later comparison.

Numeric item/block ids in a 1.7.10 save are per-world (see idmaps.py): the same number means
different items in another world, another `level.dat` or the NEI dump behind recipes.sqlite. This
tool resolves every stack of every pattern to `modid:internal_name/damage` (items) or
`fluid:<internal_name>` (fluids), keeping the numeric id only as an informational `world_id`, and
renders each pattern as one line of text.

Usage:
    python3 export_patterns.py --patterns patterns.json.gz --idmaps idmaps.json.gz \\
        --db recipes.sqlite --out-json patterns_named.json --out-txt patterns_named.txt

`--db` is optional and used *only* to attach localized names (`name`); without it every `name` is
null and the text rendering falls back to the string key. `--idmaps` must contain the registry of
the very world the scan came from (key `live` by default, `--idmap-key` to override).

Output JSON: a list 1:1 with the scan order, each entry
    {index, te, x, y, z, path, where, crafting, substitute, in: [stack|null...], out: [...], text}
with stacks
    item    {kind:"item", key:"modid:name/dmg", reg, dmg, name, n, nbt, world_id}
    fluid   {kind:"fluid", via:"fluid_drop"|"raw", key:"fluid:<internal>", fluid, name, mb[, world_id]}
    removed {kind:"removed", world_id, dmg, n}   (numeric id absent from the world registry)
`n` is the REAL stack size: the `Cnt` long tag when present (this AE2 fork writes sizes >127 and
fluid mB there, leaving `Count` at 0), else `Count`.

Output TXT: one line per pattern,
    #<index> <te> (<x>,<y>,<z>) <path> [crafting|processing]: <text>
Identity of a pattern is `(te, x, y, z, path)` — never the index, which changes between scans.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import review_matcher as rm


def build(pats, live, rdb=None):
    """-> list of export entries, 1:1 with the scan order."""
    entries = []
    for i, p in enumerate(pats):
        st = rm.resolve_stacks(p, live, rdb)
        entries.append({'index': i, 'te': p['te'], 'x': p['x'], 'y': p['y'], 'z': p['z'], 'path': p['path'],
                        'where': p['where'], 'crafting': p['crafting'], 'substitute': p.get('substitute', 0),
                        'in': st['in'], 'out': st['out'], 'text': rm.render_text(st)})
    return entries


def txt_line(e):
    return (f'#{e["index"]} {e["te"]} ({e["x"]},{e["y"]},{e["z"]}) {e["path"]} '
            f'[{"crafting" if e["crafting"] else "processing"}]: {e["text"]}')


def write_txt(entries, path):
    with open(path, 'w', encoding='utf-8') as f:
        for e in entries:
            f.write(txt_line(e) + '\n')


def write_json(entries, path):
    json.dump(entries, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--patterns', required=True, help='scan_patterns.py output (patterns.json[.gz])')
    ap.add_argument('--idmaps', required=True, help='idmaps.py output (idmaps.json[.gz])')
    ap.add_argument('--idmap-key', default='live', help='which idmaps label holds the world registry (default: %(default)s)')
    ap.add_argument('--db', default=None, help='recipes.sqlite, only to attach localized names (optional)')
    ap.add_argument('--out-json', default=None, help='output JSON path')
    ap.add_argument('--out-txt', default=None, help='output TXT path (one line per pattern)')
    a = ap.parse_args()
    if not a.out_json and not a.out_txt:
        ap.error('nothing to write: pass --out-json and/or --out-txt')

    pats = rm.load_json(a.patterns)
    live = rm.live_ids(rm.load_json(a.idmaps), a.idmap_key)
    rdb = rm.RecipeDB(a.db, names_only=True) if a.db else None
    entries = build(pats, live, rdb)
    if a.out_json:
        write_json(entries, a.out_json)
    if a.out_txt:
        write_txt(entries, a.out_txt)
    kinds = {'item': 0, 'fluid': 0, 'removed': 0}
    for e in entries:
        for s in e['in'] + e['out']:
            if s:
                kinds[s['kind']] += 1
    print(f'{len(entries)} patterns, stacks: ' + ', '.join(f'{k} {v}' for k, v in kinds.items())
          + ('' if a.db else ' (no --db: localized names are null)'), file=sys.stderr)


if __name__ == '__main__':
    main()
