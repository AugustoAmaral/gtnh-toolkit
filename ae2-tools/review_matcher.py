#!/usr/bin/env python3
"""Review AE2 encoded patterns (scan_patterns.py output) against the NEI recipe dump (gtnh-rag
recipes.sqlite) and produce a verdict per pattern. Deterministic, no LLM anywhere in this file
(gemma_review.py is an optional, separate second opinion and is NOT part of the audit pipeline).

Inputs (pass explicit paths; nothing is assumed to sit next to this script — it lives in the shared
toolkit, the data it reads is produced per scan):
  --patterns    scan_patterns.py output (required)
  --idmaps      idmaps.py output; key "live" = current world registry (required, see --idmap-key)
  --db          gtnh-rag recipes.sqlite (default: recipes.sqlite)
  --manifest    gtnh-rag data/raw/manifest.json, for the corpus label written into meta.corpus
                (default: <dir of --db>/raw/manifest.json when it exists)

Outputs: --out-json `{"meta": {...}, "results": [...]}` (results 1:1 with the scan order) and
--out-md (markdown, opens with the coverage statement).

This module is also the toolkit's shared library for "pattern -> string identity": RecipeDB,
resolve_stacks()/render_text() (used by export_patterns.py and audit.py) and the verdict machinery.

Method (deterministic, no LLM):
  * every stack is resolved to a *string* identity: `modid:internal_name/damage` for items,
    `fluid:<internal_name>` for fluids (AE2FC fluid drops and raw FluidStack entries).  Per-world
    numeric ids are kept only as an informational `world_id`; a numeric id absent from the live
    registry means the item left the pack (ITEM_REMOVED).  Stack sizes prefer the `Cnt` long tag
    over the `Count` byte (this AE2 fork writes >127 and fluid mB there, leaving Count at 0).
  * damageable items (ITEM.MAX_DAMAGE > 0) compare ignoring damage (string key `modid:name/*`);
    `gregtech:gt.integrated_circuit` is matched separately as a soft difference (GT machines also
    have a ghost circuit slot).
  * candidates = every recipe of any type whose outputs contain one of the pattern outputs.  A
    candidate is scored with integer scaling k (pattern = k x recipe, inputs and outputs), oredict
    alternatives (ITEM_GROUP_ITEM_STACKS), non-consumed inputs (stack size 0: molds, lenses,
    circuits) accepted at any amount.
  * verdicts: MATCH, MATCH_CIRCUIT_DIFF, MISMATCH (best candidate + exact diff, in string keys),
    NO_RECIPE_FOUND (every item known to the corpus, nothing produces the output),
    CORPUS_UNKNOWN_ITEM (some item/fluid of the pattern exists in the world but not in the corpus —
    the corpus cannot judge it; typically the beta-2 dump vs beta-3 world lag), ITEM_REMOVED.
"""
import argparse, collections, datetime, gzip, json, os, re, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
CIRCUIT = 'gregtech:gt.integrated_circuit'
FLUID_DROP = 'ae2fc:fluid_drop'
SIDES = {0: 'down', 1: 'up', 2: 'north', 3: 'south', 4: 'west', 5: 'east'}
VERDICTS = ('MATCH', 'MATCH_CIRCUIT_DIFF', 'MISMATCH', 'NO_RECIPE_FOUND', 'CORPUS_UNKNOWN_ITEM', 'ITEM_REMOVED')
JUDGED = ('MATCH', 'MATCH_CIRCUIT_DIFF', 'MISMATCH', 'NO_RECIPE_FOUND')
MAX_CANDIDATES_KEPT = 3
MANIFEST_KEY = 'data/raw/nesql/nesql-db.script'


def load_json(path):
    op = gzip.open if path.endswith('.gz') else open
    with op(path, 'rt') as f:
        return json.load(f)


def live_ids(maps, key='live'):
    """idmaps.py output -> {numeric item id: 'modid:name'} for one label (the world registry)."""
    if key not in maps:
        raise SystemExit(f'idmaps has no key {key!r} (has: {", ".join(sorted(maps))})')
    return {int(k.split(':')[1]): v for k, v in maps[key].items() if k.startswith('item:')}


def corpus_label(db_path, manifest=None):
    """Provenance string of the NEI/nesql dump behind recipes.sqlite, or None when no manifest."""
    cands = [manifest] if manifest else []
    cands.append(os.path.join(os.path.dirname(os.path.abspath(db_path)), 'raw', 'manifest.json'))
    for p in cands:
        if p and os.path.exists(p):
            try:
                entry = json.load(open(p, encoding='utf-8')).get(MANIFEST_KEY) or {}
            except (ValueError, OSError):
                continue
            if entry.get('source'):
                return entry['source']
    return None


# ----------------------------------------------------------------------------------------------------------------------
# String identities
# ----------------------------------------------------------------------------------------------------------------------
def item_key(reg, dmg):
    """String identity of an item stack: `modid:name/damage`; `/*` when damage is ignored (damageable item)."""
    return f'{reg}/{"*" if dmg == -1 else dmg}'


def fluid_key(name):
    return 'fluid:' + name


def key_str(key):
    """Internal match key -> string identity. Items are (reg, dmg); fluids are ('fluid', name) or a bare name."""
    if isinstance(key, tuple):
        return fluid_key(key[1]) if key[0] == 'fluid' else item_key(key[0], key[1])
    return fluid_key(key)


def clean(s):
    """Drop Minecraft colour codes from a localized name (keeps 'DEPRECATED!', which is information)."""
    return re.sub(r'§.', '', s or '').strip()


def strip_colors(s):
    return re.sub(r'§.', '', s).replace('DEPRECATED! ', '').strip()


# ----------------------------------------------------------------------------------------------------------------------
# Recipe database
# ----------------------------------------------------------------------------------------------------------------------
class RecipeDB:
    def __init__(self, path, names_only=False):
        db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        self.path = path
        self.names_only = names_only
        self.items = {}                                   # ITEM.ID -> (name, dmg, localized, max_damage)
        self.by_key = collections.defaultdict(list)       # (name, dmg) -> [ITEM.ID] (NBT-less first)
        self.name_damageable = collections.defaultdict(bool)
        for iid, mod, name, dmg, loc, maxd, nbt in db.execute(
                'select ID, MOD_ID, INTERNAL_NAME, ITEM_DAMAGE, LOCALIZED_NAME, MAX_DAMAGE, NBT from ITEM'):
            n = mod + ':' + name
            self.items[iid] = (n, dmg, loc, maxd)
            self.by_key[(n, dmg)].append((nbt not in ('', '{}'), iid))
            if maxd > 0:
                self.name_damageable[n] = True
        self.name_label = {}
        for k, v in self.by_key.items():
            v.sort()
            self.by_key[k] = [iid for _, iid in v]
            self.name_label.setdefault(k[0], self.items[self.by_key[k][0]][2])
        self.fluids = {}                                  # FLUID.ID -> (internal, localized)
        self.fluid_by_name = {}
        for fid, name, loc in db.execute('select ID, INTERNAL_NAME, LOCALIZED_NAME from FLUID'):
            self.fluids[fid] = (name, loc)
            self.fluid_by_name.setdefault(name, fid)
        if not names_only:
            self._load_recipes(db)
        db.close()
        self.by_loc = collections.defaultdict(set)
        for iid, (n, d, loc, maxd) in self.items.items():
            self.by_loc[strip_colors(loc)].add((n, -1 if maxd > 0 else d))

    def _load_recipes(self, db):
        rtype = dict(db.execute('select ID, TYPE from RECIPE_TYPE'))
        self.rtype = {rid: rtype.get(t, '?') for rid, t in db.execute('select ID, RECIPE_TYPE_ID from RECIPE')}
        self.outputs = collections.defaultdict(list)      # rid -> [(item_key, size, prob)]
        self.out_index = collections.defaultdict(set)     # item_key -> {rid}
        for rid, iid, prob, size in db.execute(
                'select RECIPE_ID, ITEM_OUTPUTS_VALUE_ITEM_ID, ITEM_OUTPUTS_VALUE_PROBABILITY, '
                'ITEM_OUTPUTS_VALUE_STACK_SIZE from RECIPE_ITEM_OUTPUTS order by RECIPE_ID, ITEM_OUTPUTS_KEY'):
            if iid in self.items:
                k = self.item_id_key(iid)
                self.outputs[rid].append((k, size, prob))
                self.out_index[k].add(rid)
        groups = collections.defaultdict(list)
        for gid, iid, size in db.execute(
                'select ITEM_GROUP_ID, ITEM_STACKS_ITEM_ID, ITEM_STACKS_STACK_SIZE from ITEM_GROUP_ITEM_STACKS'):
            if iid in self.items:
                groups[gid].append((self.item_id_key(iid), size))
        self.inputs = collections.defaultdict(list)       # rid -> [(frozenset(item_key), size)]
        for rid, gid in db.execute('select RECIPE_ID, ITEM_INPUTS_ID from RECIPE_ITEM_GROUP order by RECIPE_ID, ITEM_INPUTS_KEY'):
            g = groups.get(gid)
            if g:
                self.inputs[rid].append((frozenset(k for k, _ in g), max(s for _, s in g)))
        fgroups = collections.defaultdict(list)
        for gid, fid, amt in db.execute(
                'select FLUID_GROUP_ID, FLUID_STACKS_FLUID_ID, FLUID_STACKS_AMOUNT from FLUID_GROUP_FLUID_STACKS'):
            if fid in self.fluids:
                fgroups[gid].append((self.fluids[fid][0], amt))
        self.finputs = collections.defaultdict(list)      # rid -> [(frozenset(fluid_name), mB)]
        for rid, gid in db.execute('select RECIPE_ID, FLUID_INPUTS_ID from RECIPE_FLUID_GROUP order by RECIPE_ID, FLUID_INPUTS_KEY'):
            g = fgroups.get(gid)
            if g:
                self.finputs[rid].append((frozenset(n for n, _ in g), max(a for _, a in g)))
        self.foutputs = collections.defaultdict(list)     # rid -> [(fluid_name, mB, prob)]
        self.fout_index = collections.defaultdict(set)
        for rid, amt, fid, prob in db.execute(
                'select RECIPE_ID, FLUID_OUTPUTS_VALUE_AMOUNT, FLUID_OUTPUTS_VALUE_FLUID_ID, '
                'FLUID_OUTPUTS_VALUE_PROBABILITY from RECIPE_FLUID_OUTPUTS order by RECIPE_ID, FLUID_OUTPUTS_KEY'):
            if fid in self.fluids:
                n = self.fluids[fid][0]
                self.foutputs[rid].append((n, amt, prob))
                self.fout_index[n].add(rid)
        self.gt = {rid: (v, d, t) for rid, v, d, t in db.execute(
            'select RECIPE_ID, VOLTAGE, DURATION, VOLTAGE_TIER from GREG_TECH_RECIPE')}
        # inverted index of consumed inputs (every alternative counts), for the hints
        self.in_index = collections.defaultdict(set)
        for rid, slots in self.inputs.items():
            for alts, size in slots:
                if size:
                    for k in alts:
                        if k[0] != CIRCUIT:
                            self.in_index[k].add(rid)
        for rid, slots in self.finputs.items():
            for alts, amt in slots:
                for f in alts:
                    self.in_index[('fluid', f)].add(rid)

    def item_id_key(self, iid):
        n, d, _, maxd = self.items[iid]
        return (n, -1 if maxd > 0 else d)

    def key_for(self, name, dmg):
        """Normalized key for a world stack: damageable items collapse damage to -1. None when unknown to the DB."""
        if (name, dmg) in self.by_key:
            return (name, -1 if self.items[self.by_key[(name, dmg)][0]][3] > 0 else dmg)
        if self.name_damageable.get(name):
            return (name, -1)
        return None

    def label(self, key, precise=False):
        """Human name of an item key; with precise=True the registry key is appended when the localized name is
        shared by several items (e.g. ME Dual Interface block vs part)."""
        name, dmg = key
        if dmg == -1:
            loc = self.name_label.get(name, name)
        else:
            ids = self.by_key.get(key)
            loc = self.items[ids[0]][2] if ids else f'{name}:{dmg}'
        if precise and len(self.by_loc.get(strip_colors(loc), ())) > 1:
            loc += f' [{name}:{dmg}]'
        return loc

    def fluid_label(self, name):
        fid = self.fluid_by_name.get(name)
        return self.fluids[fid][1] if fid else name

    def fluid_localized(self, name):
        """Localized fluid name, or None when the corpus does not know this fluid."""
        fid = self.fluid_by_name.get(name)
        return self.fluids[fid][1] if fid else None


# ----------------------------------------------------------------------------------------------------------------------
# Pattern -> string identities (shared with export_patterns.py)
# ----------------------------------------------------------------------------------------------------------------------
def stack_count(s):
    """Real stack size: this AE2 fork writes sizes >127 (and fluid mB) into the `Cnt` long, leaving `Count` at 0."""
    extra = s.get('extra') or {}
    if 'Cnt' in extra:
        return int(extra['Cnt'])
    return s.get('n', 0)


def resolve_stack(s, live, rdb=None):
    """One non-empty pattern slot -> string-identity dict. `rdb=None` leaves localized names as None."""
    if 'raw' in s:                                        # AE2FC raw FluidStack entry (no item id at all)
        r = s['raw']
        f = r.get('FluidName') or r.get('Fluid') or '?'
        return {'kind': 'fluid', 'via': 'raw', 'key': fluid_key(f), 'fluid': f,
                'name': rdb.fluid_localized(f) if rdb else None, 'mb': int(r.get('Cnt', r.get('Count', 0)) or 0)}
    n = stack_count(s)
    reg = live.get(s['id'])
    if reg is None:                                       # numeric id not in the world registry: item left the pack
        return {'kind': 'removed', 'world_id': s['id'], 'dmg': s['dmg'], 'n': n}
    if reg == FLUID_DROP and s.get('tag') and 'Fluid' in s['tag']:
        f = s['tag']['Fluid']
        return {'kind': 'fluid', 'via': 'fluid_drop', 'key': fluid_key(f), 'fluid': f,
                'name': rdb.fluid_localized(f) if rdb else None, 'mb': n, 'world_id': s['id']}
    key = rdb.key_for(reg, s['dmg']) if rdb else None
    return {'kind': 'item', 'key': item_key(reg, s['dmg']), 'reg': reg, 'dmg': s['dmg'],
            'name': rdb.label(key) if key else None, 'n': n, 'nbt': s.get('tag'), 'world_id': s['id']}


def resolve_stacks(p, live, rdb=None):
    """Both sides of a pattern, 1:1 with its slots (None = empty slot)."""
    return {side: [None if s is None else resolve_stack(s, live, rdb) for s in p[side]] for side in ('in', 'out')}


def render_stack(s):
    if s['kind'] == 'fluid':
        return f'{s["mb"]}mB {clean(s["name"]) or s["key"]}'
    if s['kind'] == 'removed':
        return f'{s["n"]}x [REMOVED id {s["world_id"]}/{s["dmg"]}]'
    if s['reg'] == CIRCUIT:
        return f'circuit {s["dmg"]}'
    return f'{s["n"]}x {clean(s["name"]) or s["key"]}'


def render_text(st):
    """One-line rendering of resolved stacks: '2x Thorium Dust + 144mB Molten Tungsten -> 1x Foo'."""
    ins = ' + '.join(render_stack(s) for s in st['in'] if s)
    outs = ' + '.join(render_stack(s) for s in st['out'] if s)
    return f'{ins} -> {outs}'


def normalize(p, live, rdb, st=None):
    """-> dict with Counters: items/fluids in+out, circuits, plus removed/unknown lists and a words rendering."""
    st = st if st is not None else resolve_stacks(p, live, rdb)
    out = {'in': collections.Counter(), 'fin': collections.Counter(), 'out': collections.Counter(),
           'fout': collections.Counter(), 'circuits': collections.Counter(), 'removed': [], 'unknown': [],
           'count_unknown': False, 'words_in': [], 'words_out': [], 'stacks': st,
           'text': render_text(st)}
    for side in ('in', 'out'):
        for s in st[side]:
            if s is None:
                continue
            if s['kind'] == 'fluid':
                if s['via'] == 'fluid_drop' and s['mb'] == 0:
                    out['count_unknown'] = True
                out['f' + side][s['fluid']] += s['mb']
                out['words_' + side].append(f'{s["mb"]}mB {rdb.fluid_label(s["fluid"])}')
                if s['name'] is None:
                    out['unknown'].append(s['key'])
                continue
            if s['n'] == 0:
                out['count_unknown'] = True
            if s['kind'] == 'removed':
                out['removed'].append(f'id {s["world_id"]}:{s["dmg"]}')
                out['words_' + side].append(f'{s["n"]}x [REMOVED id {s["world_id"]}:{s["dmg"]}]')
                continue
            key = rdb.key_for(s['reg'], s['dmg'])
            if key is None:
                out['unknown'].append(s['key'])
                out['words_' + side].append(f'{s["n"]}x {s["reg"]}:{s["dmg"]} [not in the dump]')
                key = (s['reg'], s['dmg'])
            else:
                out['words_' + side].append(f'{s["n"]}x {rdb.label(key)}'
                                            + (f' (circuit {s["dmg"]})' if s['reg'] == CIRCUIT else ''))
            if side == 'in' and s['reg'] == CIRCUIT:
                out['circuits'][s['dmg']] += s['n']
            else:
                out[side][key] += s['n']
    return out


# ----------------------------------------------------------------------------------------------------------------------
# Candidate scoring
# ----------------------------------------------------------------------------------------------------------------------
def recipe_words(rdb, rid, k=1):
    ins = []
    for alts, size in rdb.inputs[rid]:
        first = min(alts, key=lambda a: (a[0] != CIRCUIT, str(a)))
        lab = rdb.label(first)
        if first[0] == CIRCUIT:
            lab = f'circuit {first[1]}'
        if len(alts) > 1:
            lab += f' (+{len(alts) - 1} alt.)'
        ins.append((f'{size * k}x ' if size else 'not consumed: ') + lab)
    for alts, amt in rdb.finputs[rid]:
        first = min(alts)
        ins.append(f'{amt * k}mB {rdb.fluid_label(first)}' + (f' (+{len(alts) - 1} alt.)' if len(alts) > 1 else ''))
    outs = [f'{size * k}x {rdb.label(key)}' + (f' ({int(prob * 100)}%)' if prob < 1 else '') for key, size, prob in rdb.outputs[rid]]
    outs += [f'{amt * k}mB {rdb.fluid_label(n)}' + (f' ({int(prob * 100)}%)' if prob < 1 else '') for n, amt, prob in rdb.foutputs[rid]]
    return ' + '.join(ins) + ' → ' + ' + '.join(outs)


def recipe_text(rdb, rid, k=1):
    """String-key rendering of a recipe, same shape as render_text() for a pattern."""
    ins = []
    for alts, size in rdb.inputs[rid]:
        first = min(alts, key=lambda a: (a[0] != CIRCUIT, str(a)))
        if first[0] == CIRCUIT:
            ins.append(f'circuit {first[1]}')
            continue
        lab = clean(rdb.label(first)) or key_str(first)
        if len(alts) > 1:
            lab += f' (+{len(alts) - 1} alt.)'
        ins.append((f'{size * k}x ' if size else 'not consumed: ') + lab)
    for alts, amt in rdb.finputs[rid]:
        first = min(alts)
        ins.append(f'{amt * k}mB {clean(rdb.fluid_label(first))}' + (f' (+{len(alts) - 1} alt.)' if len(alts) > 1 else ''))
    outs = [f'{size * k}x {clean(rdb.label(key))}' + (f' ({int(prob * 100)}%)' if prob < 1 else '')
            for key, size, prob in rdb.outputs[rid]]
    outs += [f'{amt * k}mB {clean(rdb.fluid_label(n))}' + (f' ({int(prob * 100)}%)' if prob < 1 else '')
             for n, amt, prob in rdb.foutputs[rid]]
    return ' + '.join(ins) + ' -> ' + ' + '.join(outs)


def machine_of(rdb, rid):
    t = rdb.rtype[rid]
    g = rdb.gt.get(rid)
    if g:
        return f'{t} [{g[0]} EU/t, {g[1] / 20:g}s]'
    return t


class Cmp:
    """Result of comparing one pattern with one recipe at one scale k.

    `hard`/`soft` are the human-readable difference messages (their *lengths* rank candidates), `kinds` the soft categories
    ('circuit', 'mold', 'external_fluid', 'chance').  `missing`/`extra`/`amount` are the same differences as string
    keys, `oredict` the slots where an OreDict group with several alternatives was satisfied by a specific item.
    """
    __slots__ = ('hard', 'soft', 'kinds', 'missing', 'extra', 'amount', 'oredict')

    def __init__(self):
        self.hard, self.soft, self.kinds = [], [], set()
        self.missing, self.extra, self.amount, self.oredict = [], [], [], []

    def merge(self, other):
        self.hard += other.hard
        self.soft += other.soft
        self.kinds |= other.kinds
        self.missing += other.missing
        self.extra += other.extra
        self.amount += other.amount
        self.oredict += other.oredict


def _oredict_note(rdb, alts, present, item=True):
    label = (lambda k: rdb.label(k)) if item else (lambda k: rdb.fluid_label(k))
    rec = {'slot_alternatives': len(alts), 'used': key_str(present[0]), 'used_name': clean(label(present[0]))}
    if len(present) > 1:
        rec['used_extra'] = [key_str(k) for k in present[1:]]
    return rec


def score_candidate(rdb, P, rid, k, check_outputs=True):
    """Compare pattern P with recipe rid at scale k -> Cmp."""
    c = Cmp()
    hard, soft, kinds = c.hard, c.soft, c.kinds
    remaining = collections.Counter(P['in'])
    # consumed inputs, grouped by identical alternative sets
    need = collections.OrderedDict()
    nonconsumed = []
    for alts, size in rdb.inputs[rid]:
        if size == 0:
            nonconsumed.append(alts)
        else:
            need[alts] = need.get(alts, 0) + size * k
    for alts, amt in need.items():
        present = [key for key in remaining if key in alts]
        if not present:
            first = min(alts, key=str)
            hard.append(f'missing {amt}x {rdb.label(first, precise=True)}' + (f' (or {len(alts) - 1} alt.)' if len(alts) > 1 else ''))
            c.missing.append({'side': 'in', 'kind': 'item', 'key': key_str(first), 'name': clean(rdb.label(first)),
                              'n': amt, 'alternatives': len(alts)})
            continue
        have = sum(remaining[key] for key in present)
        for key in present:
            del remaining[key]
        if len(alts) > 1:
            c.oredict.append(_oredict_note(rdb, alts, present))
        if have != amt:
            hard.append(f'{rdb.label(present[0])}: pattern has {have}, recipe wants {amt}')
            c.amount.append({'side': 'in', 'kind': 'item', 'key': key_str(present[0]),
                             'name': clean(rdb.label(present[0])), 'pattern_n': have, 'recipe_n': amt})
    pattern_circuits = collections.Counter(P['circuits'])
    for alts in nonconsumed:
        if any(a[0] == CIRCUIT for a in alts):
            cfgs = sorted(a[1] for a in alts if a[0] == CIRCUIT)
            hit = [x for x in pattern_circuits if x in cfgs]
            if hit:
                for x in hit:
                    del pattern_circuits[x]
            else:
                soft.append('recipe wants circuit ' + '/'.join(map(str, cfgs)) + (
                    f', pattern has circuit {"/".join(map(str, pattern_circuits))}' if pattern_circuits else ', pattern has no circuit'))
                kinds.add('circuit')
                pattern_circuits.clear()
            continue
        present = [key for key in remaining if key in alts]
        if present:
            for key in present:
                del remaining[key]
            if len(alts) > 1:
                c.oredict.append(_oredict_note(rdb, alts, present))
        else:
            first = min(alts, key=str)
            soft.append(f'missing non-consumed item {rdb.label(first)} (stays in the machine)')
            kinds.add('mold')
    for x, n in pattern_circuits.items():
        soft.append(f'pattern has circuit {x}, recipe uses no circuit')
        kinds.add('circuit')
    for key, n in remaining.items():
        hard.append(f'extra {n}x {rdb.label(key, precise=True)} (the recipe does not use it)')
        c.extra.append({'side': 'in', 'kind': 'item', 'key': key_str(key), 'name': clean(rdb.label(key)), 'n': n})
    # fluids
    fremaining = collections.Counter(P['fin'])
    fneed = collections.OrderedDict()
    for alts, amt in rdb.finputs[rid]:
        fneed[alts] = fneed.get(alts, 0) + amt * k
    for alts, amt in fneed.items():
        present = [f for f in fremaining if f in alts]
        if not present:
            first = min(alts)
            if not P['fin']:
                soft.append(f'pattern carries no fluid at all; recipe wants {amt}mB {rdb.fluid_label(first)} (comes in via hatch/pipe?)')
                kinds.add('external_fluid')
            else:
                hard.append(f'missing {amt}mB {rdb.fluid_label(first)}')
                c.missing.append({'side': 'in', 'kind': 'fluid', 'key': fluid_key(first),
                                  'name': clean(rdb.fluid_label(first)), 'n': amt, 'alternatives': len(alts)})
            continue
        have = sum(fremaining[f] for f in present)
        for f in present:
            del fremaining[f]
        if len(alts) > 1:
            c.oredict.append(_oredict_note(rdb, alts, present, item=False))
        if have != amt:
            hard.append(f'{rdb.fluid_label(present[0])}: pattern has {have}mB, recipe wants {amt}mB')
            c.amount.append({'side': 'in', 'kind': 'fluid', 'key': fluid_key(present[0]),
                             'name': clean(rdb.fluid_label(present[0])), 'pattern_n': have, 'recipe_n': amt})
    for f, n in fremaining.items():
        hard.append(f'extra {n}mB {rdb.fluid_label(f)} (the recipe does not use it)')
        c.extra.append({'side': 'in', 'kind': 'fluid', 'key': fluid_key(f), 'name': clean(rdb.fluid_label(f)), 'n': n})
    if not check_outputs:
        return c
    # outputs: every pattern output must be produced by the recipe, at k x size.  Guaranteed (100%) and chance
    # outputs of the same item are kept apart: the pattern may count only the sure part, or sure+chance (soft).
    c.merge(check_outputs_of(rdb, P, rid, k))
    return c


def output_totals(entries):
    """[(key, size, prob)] -> {key: (sure, chance)} with amounts summed separately by probability."""
    tot = {}
    for key, size, prob in entries:
        sure, chance = tot.get(key, (0, 0))
        tot[key] = (sure + size, chance) if prob >= 1 else (sure, chance + size)
    return tot


def check_outputs_of(rdb, P, rid, k):
    c = Cmp()
    for kind, pouts, entries in (('item', P['out'], rdb.outputs[rid]), ('fluid', P['fout'], rdb.foutputs[rid])):
        item = kind == 'item'
        label = (lambda key: rdb.label(key)) if item else (lambda key: rdb.fluid_label(key))
        skey = (lambda key: key_str(key)) if item else fluid_key
        tot = output_totals(entries)
        unit = '' if item else 'mB'
        for key, n in pouts.items():
            if key not in tot:
                c.hard.append(f'output {label(key)} is not produced by this recipe')
                c.extra.append({'side': 'out', 'kind': kind, 'key': skey(key), 'name': clean(label(key)), 'n': n})
                continue
            sure, chance = tot[key]
            if n == sure * k and sure:
                continue
            if n == (sure + chance) * k:
                c.soft.append(f'output {label(key)}: pattern counts on the chance output ({sure * k}{unit} guaranteed + {chance * k}{unit} chance)')
                c.kinds.add('chance')
                continue
            c.hard.append(f'output {label(key)}: pattern says {n}{unit}, recipe gives {sure * k}{unit}' + (f' (+{chance * k}{unit} chance)' if chance else ''))
            c.amount.append({'side': 'out', 'kind': kind, 'key': skey(key), 'name': clean(label(key)),
                             'pattern_n': n, 'recipe_n': sure * k})
    return c


def scale_candidates(rdb, P, rid):
    """Integer scales k worth trying: from output ratio, then from any consumed input ratio, always 1."""
    ks = []
    pouts = dict(P['out'])
    pouts.update({('fluid', f): n for f, n in P['fout'].items()})
    tot = output_totals(rdb.outputs[rid])
    tot.update({('fluid', f): v for f, v in output_totals(rdb.foutputs[rid]).items()})
    for key, n in pouts.items():
        if key in tot:
            for base in (tot[key][0], tot[key][0] + tot[key][1]):
                if base and n % base == 0:
                    ks.append(n // base)
    for alts, size in rdb.inputs[rid]:
        if size:
            have = sum(n for key, n in P['in'].items() if key in alts)
            if have and have % size == 0:
                ks.append(have // size)
    for alts, amt in rdb.finputs[rid]:
        have = sum(n for f, n in P['fin'].items() if f in alts)
        if have and have % amt == 0:
            ks.append(have // amt)
    ks.append(1)
    seen, uniq = set(), []
    for k in ks:
        if k >= 1 and k not in seen:
            seen.add(k)
            uniq.append(k)
    return uniq


def rank_key(rdb, rid, hard, soft):
    t = rdb.rtype[rid]
    is_crafting = t.startswith('Crafting') or t.startswith('Extreme Crafting') or t.startswith('Magical Crafting')
    return (len(hard), len(soft), is_crafting, t)


def evaluate(rdb, P, crafting):
    """-> (best, scored) where each entry is (rank, rid, k, Cmp); best is None when no recipe produces the output."""
    cands = set()
    for key in P['out']:
        cands |= rdb.out_index.get(key, set())
    for f in P['fout']:
        cands |= rdb.fout_index.get(f, set())
    if crafting:
        cands = {r for r in cands if rdb.rtype[r].startswith('Crafting')}
    if not cands:
        return None, []
    scored = []
    for rid in cands:
        best = None
        for k in scale_candidates(rdb, P, rid):
            c = score_candidate(rdb, P, rid, k)
            cand = (rank_key(rdb, rid, c.hard, c.soft), k, c)
            if best is None or cand[0] < best[0]:
                best = cand
            if not c.hard and not c.soft:
                break
        scored.append((best[0], rid, best[1], best[2]))
    scored.sort(key=lambda c: (c[0], c[1]))
    return scored[0], scored


def chain_hint(rdb, P):
    """MISMATCH helper: is there a recipe whose *inputs* are exactly the pattern's inputs but whose output differs?
    (typical of multi-step patterns, e.g. 'X Dust -> X Ingot' that really is EBF -> Hot Ingot -> Vacuum Freezer).
    Deterministic: exact input-set intersection, the same scorer without outputs, then a second step whose only
    consumed input is the first step's output and which does produce the pattern output.  Returns a string or None."""
    keys = list(P['in']) + [('fluid', f) for f in P['fin']]
    if not keys:
        return None
    sets = [rdb.in_index.get(k) for k in keys]
    if any(s is None for s in sets):
        return None
    cands = set.intersection(*sets) if len(sets) > 1 else set(sets[0])
    want = [rdb.label(k) for k in P['out']] + [rdb.fluid_label(f) for f in P['fout']]
    best = None
    for rid in cands:
        if not rdb.outputs[rid] and not rdb.foutputs[rid]:
            continue
        for k in scale_candidates(rdb, P, rid):
            c = score_candidate(rdb, P, rid, k, check_outputs=False)
            if c.hard:
                continue
            outs = [rdb.label(o) for o, _, _ in rdb.outputs[rid]] + [rdb.fluid_label(f) for f, _, _ in rdb.foutputs[rid]]
            related = any(w in o or o in w for w in want for o in outs)
            cand = (not related, len(c.soft), rdb.rtype[rid].startswith('Crafting'), rdb.rtype[rid], rid, k)
            if best is None or cand < best:
                best = cand
            break
    if best is None:
        return None
    rid, k = best[4], best[5]
    text = f'the inputs match exactly with {machine_of(rdb, rid)}' + (f' ×{k}' if k > 1 else '') + f': {recipe_words(rdb, rid, k)}'
    # second step: single-input recipe from one of this recipe's outputs to the pattern output
    for o, size, prob in rdb.outputs[rid]:
        step2 = set()
        for key in P['out']:
            step2 |= rdb.out_index.get(key, set())
        step2 &= rdb.in_index.get(o, set())
        P2 = {'in': collections.Counter({o: size * k}), 'fin': collections.Counter(), 'out': P['out'],
              'fout': P['fout'], 'circuits': collections.Counter()}
        for rid2 in sorted(step2, key=lambda r: rdb.rtype[r]):
            for k2 in scale_candidates(rdb, P2, rid2):
                if not score_candidate(rdb, P2, rid2, k2).hard:
                    return text + f' ⇒ and {size * k}x {rdb.label(o)} becomes the pattern output in {machine_of(rdb, rid2)}' + (
                        f' ×{k2}' if k2 > 1 else '') + f': {recipe_words(rdb, rid2, k2)} (2-step pattern)'
    return text


def renamed_hint(rdb, P):
    """NO_RECIPE_FOUND helper: an output item with no producer whose localized name exists under another damage/id
    that *does* have producers (GT++ machines renumbered in 2.9).  Evaluates the pattern against those recipes."""
    hints = []
    for key in P['out']:
        if rdb.out_index.get(key):
            continue
        loc = strip_colors(rdb.label(key))
        others = [k2 for k2 in rdb.by_loc.get(loc, ()) if k2 != key and rdb.out_index.get(k2)]
        if not others:
            continue
        P2 = dict(P)
        P2['out'] = collections.Counter({(others[0] if k == key else k): n for k, n in P['out'].items()})
        best, _ = evaluate(rdb, P2, False)
        if best is None:
            continue
        _, rid, k, c = best
        status = 'inputs match' if not c.hard else 'inputs differ: ' + '; '.join(c.hard)
        hints.append(f'output {rdb.label(key)} ({key[0]}:{key[1]}) has no recipe, but the same-name item '
                     f'{others[0][0]}:{others[0][1]} does have one ({machine_of(rdb, rid)}: {recipe_words(rdb, rid, k)}) — {status}')
    return hints


# ----------------------------------------------------------------------------------------------------------------------
# Locations
# ----------------------------------------------------------------------------------------------------------------------
def side_slot(p):
    m = re.match(r'^/extra:(\d+)/patterns/#(\d+)$', p['path'])
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r'^/patterns/#(\d+)$', p['path'])
    if m:
        return None, int(m.group(1))
    m = re.match(r'^/Inventory\[(\d+)\]$', p['path'])
    if m:
        return None, int(m.group(1))
    return None, None


def te_label(p):
    return {'BlockInterface': 'ME Interface (block)', 'BlockCableBus': 'ME Interface (cable part)',
            'fluid_interface': 'Dual Interface (AE2FC)', 'player': 'player inventory'}.get(p['te'], p['te'])


def location(p):
    side, slot = side_slot(p)
    if p['te'] == 'player':
        return f'player inventory, slot {slot}'
    s = f'{te_label(p)} at ({p["x"]}, {p["y"]}, {p["z"]})'
    if side is not None:
        s += f', side {SIDES[side]}'
    return s + (f', slot {slot}' if slot is not None else f', {p["path"]}')


# ----------------------------------------------------------------------------------------------------------------------
def review(pats, live, rdb, stacks=None):
    """Verdict for every pattern, in scan order. `stacks` = pre-resolved stacks (export_patterns.resolve_all)."""
    results = []
    for i, p in enumerate(pats):
        P = normalize(p, live, rdb, st=stacks[i] if stacks else None)
        side, slot = side_slot(p)
        entry = {'index': i, 'te': p['te'], 'x': p['x'], 'y': p['y'], 'z': p['z'], 'side': side, 'slot': slot,
                 'path': p['path'], 'where': p['where'], 'crafting': p['crafting'], 'location': location(p),
                 'pattern': ' + '.join(P['words_in']) + ' → ' + ' + '.join(P['words_out']),
                 'text': P['text'], 'count_unknown': P['count_unknown'], 'verdict': None,
                 'corpus_unknown': P['unknown'], 'candidate': None, 'candidate_machine': None, 'candidate_text': None,
                 'diff': [], 'diff_keys': None, 'via_oredict': [], 'soft_diff': [], 'soft_kinds': [],
                 'alternatives': [], 'chain_hint': None, 'renamed_hint': []}
        if P['removed']:
            entry['verdict'] = 'ITEM_REMOVED'
            entry['diff'] = [f'item removed from the pack: {r}' for r in P['removed']]
            results.append(entry)
            continue
        best, scored = evaluate(rdb, P, p['crafting'])
        if best is None:
            if P['unknown']:
                entry['verdict'] = 'CORPUS_UNKNOWN_ITEM'
                entry['diff'] = [f'world item/fluid that is not in the recipe corpus: {u}' for u in P['unknown']]
            else:
                entry['verdict'] = 'NO_RECIPE_FOUND'
                entry['diff'] = ['no recipe in the corpus produces ' + ' + '.join(P['words_out'])]
            entry['renamed_hint'] = renamed_hint(rdb, P)
            results.append(entry)
            continue
        _, rid, k, c = best
        entry['verdict'] = 'MATCH' if not c.hard and not c.soft else ('MATCH_CIRCUIT_DIFF' if not c.hard else 'MISMATCH')
        entry['candidate'] = recipe_words(rdb, rid, k)
        entry['candidate_text'] = recipe_text(rdb, rid, k)
        entry['candidate_machine'] = machine_of(rdb, rid) + (f' ×{k}' if k > 1 else '')
        entry['candidate_rid'] = rid
        entry['scale'] = k
        entry['diff'] = c.hard
        entry['soft_diff'] = c.soft
        entry['soft_kinds'] = sorted(c.kinds)
        entry['n_candidates'] = len(scored)
        entry['via_oredict'] = c.oredict
        if c.hard:
            entry['diff_keys'] = {'missing': c.missing, 'extra': c.extra, 'amount': c.amount,
                                  'pattern_text': P['text'], 'candidate_text': entry['candidate_text']}
            entry['chain_hint'] = chain_hint(rdb, P)
        # other machines that give the same best result (e.g. Mixer vs Multiblock Mixer)
        for rk, rid2, k2, c2 in scored[1:MAX_CANDIDATES_KEPT + 3]:
            if rid2 != rid and (rk[0], rk[1]) == (len(c.hard), len(c.soft)) and len(entry['alternatives']) < 3:
                entry['alternatives'].append(machine_of(rdb, rid2) + (f' ×{k2}' if k2 > 1 else ''))
        # top-3 distinct candidates, for the optional gemma_review.py pass
        entry['top_candidates'] = []
        seen = set()
        for rk, rid2, k2, c2 in scored:
            w = recipe_words(rdb, rid2, k2)
            if w in seen:
                continue
            seen.add(w)
            entry['top_candidates'].append({'machine': machine_of(rdb, rid2), 'scale': k2, 'recipe': w,
                                            'recipe_base': recipe_words(rdb, rid2, 1), 'hard': c2.hard, 'soft': c2.soft})
            if len(entry['top_candidates']) == MAX_CANDIDATES_KEPT:
                break
        results.append(entry)
    return results


# ----------------------------------------------------------------------------------------------------------------------
# Meta + coverage statement
# ----------------------------------------------------------------------------------------------------------------------
def build_meta(results, patterns_path, idmaps_path, idmap_key, db_path, corpus, label=None):
    counts = collections.Counter(r['verdict'] for r in results)
    meta = {'generated': datetime.date.today().isoformat(), 'patterns_path': patterns_path,
            'idmaps_path': idmaps_path, 'idmap_key': idmap_key, 'db_path': db_path, 'corpus': corpus,
            'counts': {v: counts.get(v, 0) for v in VERDICTS},
            'coverage': {'total': len(results),
                         'judged': sum(counts.get(v, 0) for v in JUDGED),
                         'unjudged_corpus_unknown': counts.get('CORPUS_UNKNOWN_ITEM', 0),
                         'unjudged_item_removed': counts.get('ITEM_REMOVED', 0)}}
    if label is not None:
        meta['label'] = label
    return meta


def coverage_text(meta, results):
    """The plain-words coverage statement: what the corpus could judge and what it could not.
    Used verbatim as audit.py's summary.txt and as the opening of the .md report."""
    c, cov = meta['counts'], meta['coverage']
    total, judged = cov['total'], cov['judged']
    ok = c['MATCH'] + c['MATCH_CIRCUIT_DIFF']
    ored = sum(1 for r in results if r['verdict'] in ('MATCH', 'MATCH_CIRCUIT_DIFF') and r['via_oredict'])
    mism_unknown = sum(1 for r in results if r['verdict'] == 'MISMATCH' and r['corpus_unknown'])
    pct = (100.0 * judged / total) if total else 0.0
    L = [f'AE2 pattern audit — generated on {meta["generated"]}'
         + (f' (run: {meta["label"]})' if meta.get('label') else '')]
    L.append(f'Recipe corpus: {meta["corpus"] or "unknown (no manifest found next to recipes.sqlite)"}')
    L.append(f'Patterns read: {total} — from `{os.path.basename(meta["patterns_path"])}`, '
             f'ids resolved through the "{meta["idmap_key"]}" key of `{os.path.basename(meta["idmaps_path"])}`')
    L.append('')
    L.append(f'The corpus could judge {judged} of the {total} patterns ({pct:.1f}%):')
    L.append(f'- {ok} match a recipe in the corpus:')
    L.append(f'  - {c["MATCH"]} match exactly (`MATCH`)')
    L.append(f'  - {c["MATCH_CIRCUIT_DIFF"]} match with a soft difference (`MATCH_CIRCUIT_DIFF`): programmed circuit, '
             'non-consumed item that stays in the machine (mold/shape/lens) or fluid the pattern does not carry')
    L.append(f'  - of those {ok}, {ored} matched using an OreDict alternative in some slot '
             '(`via_oredict` field in the JSON: shows which specific item went into a slot that accepts several)')
    L.append(f'- {c["MISMATCH"]} differ from the best candidate recipe (`MISMATCH`) — each one listed in the md '
             'report, with location, best candidate and the exact difference')
    L.append(f'- {c["NO_RECIPE_FOUND"]} have no recipe in the corpus that produces the output (`NO_RECIPE_FOUND`) '
             '— listed in the md report')
    L.append('')
    L.append(f'The corpus CANNOT judge {cov["unjudged_corpus_unknown"] + cov["unjudged_item_removed"]} patterns:')
    L.append(f'- {cov["unjudged_corpus_unknown"]} use an item/fluid that exists in the world but is not in the corpus '
             '(`CORPUS_UNKNOWN_ITEM`) — this is the signal of version lag between the corpus and the world; listed in '
             'the md report')
    L.append(f'- {cov["unjudged_item_removed"]} use an item id that no longer exists in the world registry '
             '(`ITEM_REMOVED`) — those are genuinely broken; listed in the md report')
    L.append('')
    L.append(f'How to read this: "nothing else is broken" only holds for the {ok} that match, with the caveats at the '
             f'end of the md report (a soft difference is an assumption). What needs human eyes is '
             f'{c["MISMATCH"]} + {c["NO_RECIPE_FOUND"]} + {cov["unjudged_corpus_unknown"]} + {cov["unjudged_item_removed"]} '
             f'= {total - ok} patterns.')
    if mism_unknown:
        L.append(f'Caveat about the `MISMATCH` ones: {mism_unknown} '
                 + ('of them uses' if mism_unknown == 1 else 'of them use')
                 + ' some item/fluid the corpus does not know (`corpus_unknown` field), so the difference may be '
                   'just corpus lag.')
    return '\n'.join(L)


# ----------------------------------------------------------------------------------------------------------------------
def write_md(results, meta, path):
    counts = collections.Counter(r['verdict'] for r in results)
    L = ['# AE2 pattern audit against the recipe corpus\n']
    L.append(coverage_text(meta, results) + '\n')
    L.append('## Count by verdict\n')
    L.append('| verdict | patterns |\n|---|---|')
    for v in VERDICTS:
        L.append(f'| `{v}` | {counts.get(v, 0)} |')
    L.append(f'| **total** | **{len(results)}** |\n')
    L.append('Meaning: `MATCH` = inputs and outputs match a recipe in the corpus (including an integer multiple k); '
             '`MATCH_CIRCUIT_DIFF` = matches except for one "soft difference": programmed circuit (the machine may '
             'have the ghost circuit configured), non-consumed item that stays in the machine (mold, extruder shape, '
             'lens, inscriber press) or fluid the pattern does not carry (only when the pattern has no fluid at all — '
             'it must come in via hatch/pipe); `MISMATCH` = a recipe for the output exists but the inputs differ (the '
             'best candidate and the exact difference are listed; "the inputs match exactly with…" points at the '
             'recipe that consumes exactly those inputs but produces something else — multi-step pattern or wrong '
             'output); `NO_RECIPE_FOUND` = every item exists in the corpus, but nothing in it produces the output; '
             '`CORPUS_UNKNOWN_ITEM` = some item/fluid of the pattern exists in the world and not in the corpus, so the '
             'corpus cannot judge it (version lag); `ITEM_REMOVED` = item id that no longer exists in the world '
             'registry (genuinely broken pattern).\n')
    L.append('Cable part side: 0=down 1=up 2=north 3=south 4=west 5=east. `k` = integer multiple of the recipe. '
             'The identity of a pattern is `(te, x, y, z, path)` — the `#index` below is only the scan order and '
             'changes between scans.\n')
    soft_counts = collections.Counter()
    for r in results:
        if r['verdict'] == 'MATCH_CIRCUIT_DIFF':
            soft_counts[' + '.join(r['soft_kinds'])] += 1
    L.append('`MATCH_CIRCUIT_DIFF` by kind of soft difference: ' + ', '.join(f'{k}: {n}' for k, n in soft_counts.most_common()) + '.\n')

    def block(r, with_candidate=True):
        s = [f'- **#{r["index"]}** — {r["location"]} — {"crafting" if r["crafting"] else "processing"}']
        s.append(f'  - Pattern: {r["pattern"]}')
        if with_candidate and r['candidate']:
            s.append(f'  - Best candidate ({r["candidate_machine"]}): {r["candidate"]}')
            if r['alternatives']:
                s.append('  - Same result in: ' + '; '.join(r['alternatives']))
        for d in r['diff']:
            s.append(f'  - Difference: {d}')
        for d in r['soft_diff']:
            s.append(f'  - Soft difference: {d}')
        for o in r['via_oredict']:
            s.append(f'  - Via OreDict: slot with {o["slot_alternatives"]} alternatives, the pattern uses '
                     f'{o["used_name"]} (`{o["used"]}`)')
        if r['corpus_unknown']:
            s.append('  - Outside the corpus: ' + ', '.join(f'`{u}`' for u in r['corpus_unknown']))
        if r.get('chain_hint'):
            s.append(f'  - Hint: {r["chain_hint"]}')
        for h in r.get('renamed_hint') or []:
            s.append(f'  - Hint: {h}')
        if r['count_unknown']:
            s.append('  - Warning: stack size not recovered from the scan for some stack (Count=0 with no Cnt)')
        return '\n'.join(s)

    for v, title in (('ITEM_REMOVED', 'Item removed from the pack (genuinely broken pattern)'),
                     ('CORPUS_UNKNOWN_ITEM', 'The corpus does not know some item/fluid of the pattern (not judgeable)'),
                     ('NO_RECIPE_FOUND', 'No recipe in the corpus produces the output'),
                     ('MISMATCH', 'Inputs differ from the best candidate recipe'),
                     ('MATCH_CIRCUIT_DIFF', 'Matches except for a soft difference (circuit / non-consumed item / external fluid)')):
        rs = [r for r in results if r['verdict'] == v]
        L.append(f'## {title} — `{v}` ({len(rs)})\n')
        if v == 'MISMATCH':
            kinds = collections.Counter()
            for r in rs:
                for d in r['diff']:
                    kinds[re.sub(r'\d+', 'N', d)] += 1
            L.append('Most frequent kinds of difference (numbers replaced by N):\n')
            for kind, n in kinds.most_common(25):
                L.append(f'- {n}× {kind}')
            L.append('')
        if v == 'MATCH_CIRCUIT_DIFF':
            order = {'external_fluid': 0, 'mold': 1, 'chance': 2, 'circuit': 3}
            rs = sorted(rs, key=lambda r: (min(order.get(k, 9) for k in r['soft_kinds']) if r['soft_kinds'] else 9, r['index']))
            L.append('Order: external fluid first (the riskiest: the recipe may have gained a new fluid), '
                     'then mold/shape/lens, then circuit only.\n')
        for r in rs:
            L.append(block(r))
        L.append('')
    rs = [r for r in results if r['verdict'] == 'MATCH']
    L.append('## Caveats\n')
    L.append(f'- **Corpus version vs world version.** The recipes come from `{os.path.basename(meta["db_path"])}` '
             f'({meta["corpus"] or "unknown provenance"}); the world may be on another version of the pack. A recipe '
             'that changed between the two shows up as `MISMATCH` (if it ended up different) or as `MATCH` (if the '
             'pattern was encoded on the corpus version and the recipe changed afterwards — invisible here). The '
             '`CORPUS_UNKNOWN_ITEM` ones are the direct measure of that lag. When in doubt, in-game NEI wins.')
    L.append('- **A processing pattern does not store the machine.** The matcher searches ALL recipes that produce the '
             'output (any type, GT included) and picks the one that matches best; "best candidate" is an inference '
             'from the contents, not from the machine the interface is attached to. When another machine gives the '
             'same result, it shows up under "Same result in".')
    L.append('- **Soft differences are assumptions.** A missing circuit (the machine may have the ghost circuit), a '
             'missing mold/shape/lens (it stays inside the machine) and a missing fluid when the pattern carries no '
             'fluid at all (it comes in via hatch/pipe) do NOT break the pattern by themselves — but if the recipe '
             'GAINED a new fluid/circuit in an update, the pattern is broken and this report cannot see it. The '
             'external-fluid cases are listed first in the `MATCH_CIRCUIT_DIFF` section for manual checking.')
    L.append('- **Stack sizes.** The size of each stack comes from `Cnt` (long) when it exists, otherwise from '
             '`Count` (byte, max. 127) — this AE2 fork leaves `Count=0` and writes the real number into `Cnt`. '
             'Reading only `Count` got hundreds of patterns wrong in the reference dataset. Where neither `Cnt` nor '
             '`Count` exists, the pattern carries the "stack size not recovered" warning.')
    L.append('- **Secondary outputs.** A pattern may omit outputs of the recipe (chance outputs, by-products) without '
             'that counting as a difference; only an output listed in the pattern that the recipe does not produce, '
             'or one with the wrong amount, counts.')
    L.append('- **Items with NBT.** Comparison by (registry, damage), ignoring NBT (Assembly Line data sticks do not '
             'enter the inputs; GT tools and electric items compare ignoring damage/charge — key `modid:name/*`).')
    L.append('- **OreDict.** A recipe slot may accept several items; when the pattern uses one of them the verdict is '
             '`MATCH` and the specific item is recorded in `via_oredict` (and in the lists above), so that an '
             '`any:stick` matched by one specific stick is not hidden.')
    L.append('- **2-step pattern.** "Hint: … (2-step pattern)" is a deterministic detection: one recipe consumes '
             'exactly the inputs, and a second single-input recipe leads to the pattern product. It does not claim '
             'how the flow is wired in the base — only that a 2-recipe path exists.')
    L.append(f'- **Crafting patterns ({sum(1 for r in results if r["crafting"])}).** They went through the same '
             'pipeline but only against table recipes, by multiset (no position/mirroring).')
    L.append('')
    L.append(f'## Appendix — `MATCH` ({len(rs)}), one line per pattern\n')
    for r in rs:
        L.append(f'- #{r["index"]} {r["location"]} — {r["candidate_machine"]} — {r["pattern"]}')
    L.append('')
    open(path, 'w', encoding='utf-8').write('\n'.join(L))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--patterns', required=True, help='scan_patterns.py output (patterns.json[.gz])')
    ap.add_argument('--idmaps', required=True, help='idmaps.py output (idmaps.json[.gz])')
    ap.add_argument('--idmap-key', default='live', help='which idmaps label holds the world registry (default: %(default)s)')
    ap.add_argument('--db', default='recipes.sqlite')
    ap.add_argument('--manifest', default=None, help='gtnh-rag data/raw/manifest.json for the corpus label in meta.corpus')
    ap.add_argument('--label', default=None, help='free text stored in meta.label (e.g. post-fix)')
    ap.add_argument('--out-json', default='review_all.json')
    ap.add_argument('--out-md', default='review_all.md')
    a = ap.parse_args()

    pats = load_json(a.patterns)
    live = live_ids(load_json(a.idmaps), a.idmap_key)
    rdb = RecipeDB(a.db)
    results = review(pats, live, rdb)
    meta = build_meta(results, a.patterns, a.idmaps, a.idmap_key, a.db, corpus_label(a.db, a.manifest), a.label)
    json.dump({'meta': meta, 'results': results}, open(a.out_json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    write_md(results, meta, a.out_md)
    print(coverage_text(meta, results))
    print()
    print(collections.Counter(r['verdict'] for r in results))


if __name__ == '__main__':
    main()
