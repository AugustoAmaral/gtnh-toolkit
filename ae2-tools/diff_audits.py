#!/usr/bin/env python3
"""Diff two audit directories written by audit.py: what moved between two rounds of pattern work.

Patterns are keyed by their identity `(te, x, y, z, path)` — **the scan index is NOT identity**: the
number of patterns changes between scans (a player pulling patterns out of the ME network into a
chest shifts every index), so anything keyed by index would report nonsense.

Usage:
    python3 diff_audits.py audits/<date-a> audits/<date-b> --out diff.md

Reports (markdown + the same data as JSON next to it):
  * the corpus label of each run, so a corpus change (e.g. beta-2 -> beta-3 dump) is visible in the
    header and never mistaken for a change in the world;
  * patterns only in A, only in B;
  * same identity, different content (the `text` rendering changed — the pattern was re-encoded);
  * verdict transitions (`MISMATCH -> MATCH`, ...), counted and listed.
Transitions read A -> B, in the order the directories were given on the command line.
"""
import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import review_matcher as rm


def load_run(d):
    """-> (meta, {identity: entry}) for one audit dir; identity = (te, x, y, z, path)."""
    rev_path, named_path = os.path.join(d, 'review.json'), os.path.join(d, 'patterns_named.json')
    for p in (rev_path, named_path):
        if not os.path.exists(p):
            raise SystemExit(f'{d}: not an audit dir ({os.path.basename(p)} missing) — run audit.py first')
    rev = rm.load_json(rev_path)
    if not isinstance(rev, dict) or 'results' not in rev:
        raise SystemExit(f'{rev_path}: expected the {{meta, results}} shape written by review_matcher.py')
    text = {e['index']: e['text'] for e in rm.load_json(named_path)}
    by_id, dupes = {}, []
    for r in rev['results']:
        ident = (r['te'], r['x'], r['y'], r['z'], r['path'])
        entry = {'identity': list(ident), 'index': r['index'], 'location': r['location'],
                 'crafting': r['crafting'], 'verdict': r['verdict'], 'text': text.get(r['index'], r.get('text'))}
        if ident in by_id:
            dupes.append(ident)
            continue
        by_id[ident] = entry
    if dupes:
        print(f'WARN: {d}: {len(dupes)} duplicated identities ignored: {dupes[:3]}', file=sys.stderr)
    meta = dict(rev['meta'])
    meta['dir'] = os.path.abspath(d)
    meta['patterns'] = len(rev['results'])
    return meta, by_id


def describe(meta):
    return (f'`{meta["dir"]}` — {meta["patterns"]} patterns, generated at {meta.get("generated")}'
            + (f', run "{meta["label"]}"' if meta.get('label') else '')
            + f'\n  - corpus: {meta.get("corpus") or "unknown"}')


def build_diff(ma, A, mb, B):
    only_a = [A[i] for i in A if i not in B]
    only_b = [B[i] for i in B if i not in A]
    common = [i for i in A if i in B]
    changed = [{'identity': list(i), 'location': A[i]['location'],
                'a': {'index': A[i]['index'], 'text': A[i]['text'], 'verdict': A[i]['verdict']},
                'b': {'index': B[i]['index'], 'text': B[i]['text'], 'verdict': B[i]['verdict']}}
               for i in common if A[i]['text'] != B[i]['text']]
    trans = collections.defaultdict(list)
    stable = collections.Counter()
    for i in common:
        va, vb = A[i]['verdict'], B[i]['verdict']
        if va == vb:
            stable[va] += 1
        else:
            trans[f'{va} -> {vb}'].append({'identity': list(i), 'location': A[i]['location'],
                                           'a_index': A[i]['index'], 'b_index': B[i]['index'],
                                           'text_a': A[i]['text'], 'text_b': B[i]['text']})
    return {'a': ma, 'b': mb,
            'corpus_changed': ma.get('corpus') != mb.get('corpus'),
            'counts': {'a_patterns': ma['patterns'], 'b_patterns': mb['patterns'], 'common': len(common),
                       'only_in_a': len(only_a), 'only_in_b': len(only_b), 'content_changed': len(changed),
                       'verdict_changed': sum(len(v) for v in trans.values()),
                       'verdict_stable': dict(sorted(stable.items()))},
            'only_in_a': only_a, 'only_in_b': only_b, 'content_changed': changed,
            'verdict_transitions': {k: v for k, v in sorted(trans.items(), key=lambda kv: (-len(kv[1]), kv[0]))}}


def write_md(D, path, limit):
    ma, mb, c = D['a'], D['b'], D['counts']

    def listing(title, rows, render):
        L = [f'## {title} ({len(rows)})\n']
        for r in rows[:limit]:
            L.append(render(r))
        if len(rows) > limit:
            L.append(f'- … and {len(rows) - limit} more (full list in the JSON next to it)')
        L.append('')
        return L

    L = ['# Diff of two AE2 pattern audits\n']
    L.append(f'- **A** = {describe(ma)}')
    L.append(f'- **B** = {describe(mb)}')
    L.append('')
    L.append(('⚠ **The two sides use different recipe corpora** — part of the verdict changes below may be a change '
              'of corpus, not a change in the world.' if D['corpus_changed']
              else 'Both sides use the same recipe corpus, so a verdict change is a change in the world.'))
    L.append('')
    L.append('The identity of a pattern is `(te, x, y, z, path)`. **The scan `#index` is NOT identity** — the total '
             'number of patterns changes between scans (pulling patterns out of the ME network into a chest shifts '
             'every index), so every comparison below is by identity; the indices appear only as a cross-reference '
             "to each side's report. The transitions read A -> B, in the order the directories were given.\n")
    L.append('| | patterns |\n|---|---|')
    L.append(f'| A | {c["a_patterns"]} |')
    L.append(f'| B | {c["b_patterns"]} |')
    L.append(f'| identities in common | {c["common"]} |')
    L.append(f'| only in A | {c["only_in_a"]} |')
    L.append(f'| only in B | {c["only_in_b"]} |')
    L.append(f'| same identity, different content | {c["content_changed"]} |')
    L.append(f'| same identity, different verdict | {c["verdict_changed"]} |')
    L.append('')
    if D['verdict_transitions']:
        L.append('## Verdict transitions\n')
        L.append('| A | B | patterns |\n|---|---|---|')
        for k, v in D['verdict_transitions'].items():
            va, vb = k.split(' -> ')
            L.append(f'| `{va}` | `{vb}` | {len(v)} |')
        L.append('')
        for k, v in D['verdict_transitions'].items():
            L.append(f'### `{k}` ({len(v)})\n')
            for r in v[:limit]:
                L.append(f'- {r["location"]} (A #{r["a_index"]}, B #{r["b_index"]})')
                L.append(f'  - A: {r["text_a"]}')
                if r['text_b'] != r['text_a']:
                    L.append(f'  - B: {r["text_b"]}')
            if len(v) > limit:
                L.append(f'- … and {len(v) - limit} more (full list in the JSON next to it)')
            L.append('')
    else:
        L.append('## Verdict transitions\n\nNone: every common identity kept its verdict.\n')
    L.append('Verdicts that did not change: ' + (', '.join(f'`{k}` {n}' for k, n in c['verdict_stable'].items()) or 'none') + '.\n')
    L += listing('Only in A', D['only_in_a'],
                 lambda r: f'- {r["location"]} — `{r["verdict"]}` (A #{r["index"]})\n  - {r["text"]}')
    L += listing('Only in B', D['only_in_b'],
                 lambda r: f'- {r["location"]} — `{r["verdict"]}` (B #{r["index"]})\n  - {r["text"]}')
    L += listing('Same identity, different content (pattern re-encoded)', D['content_changed'],
                 lambda r: (f'- {r["location"]} (A #{r["a"]["index"]} `{r["a"]["verdict"]}`, '
                            f'B #{r["b"]["index"]} `{r["b"]["verdict"]}`)\n'
                            f'  - A: {r["a"]["text"]}\n  - B: {r["b"]["text"]}'))
    open(path, 'w', encoding='utf-8').write('\n'.join(L))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('a_dir', help='audit dir A (audit.py --out-dir)')
    ap.add_argument('b_dir', help='audit dir B')
    ap.add_argument('--out', required=True, help='output markdown path (the JSON goes next to it, same stem)')
    ap.add_argument('--out-json', default=None, help='override the JSON path (default: --out with a .json suffix)')
    ap.add_argument('--limit', type=int, default=200, help='max patterns listed per section in the md (default: %(default)s)')
    a = ap.parse_args()

    ma, A = load_run(a.a_dir)
    mb, B = load_run(a.b_dir)
    D = build_diff(ma, A, mb, B)
    write_md(D, a.out, a.limit)
    out_json = a.out_json or (a.out[:-3] if a.out.endswith('.md') else a.out) + '.json'
    json.dump(D, open(out_json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    c = D['counts']
    print(f'A {c["a_patterns"]} patterns, B {c["b_patterns"]}, common {c["common"]}; only in A {c["only_in_a"]}, '
          f'only in B {c["only_in_b"]}, content changed {c["content_changed"]}, verdict changed {c["verdict_changed"]}'
          + ('; DIFFERENT CORPUS' if D['corpus_changed'] else ''))
    print(f'{a.out} + {out_json}', file=sys.stderr)


if __name__ == '__main__':
    main()
