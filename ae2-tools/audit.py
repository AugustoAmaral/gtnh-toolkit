#!/usr/bin/env python3
"""One command, one directory: the whole deterministic AE2 pattern audit of a save.

Reads the save (or an existing scan), resolves every encoded pattern to string identities, judges
each one against the NEI recipe corpus (recipes.sqlite) and writes a self-contained audit directory
whose `summary.txt` is the coverage statement — "of N patterns the corpus could judge M; X match,
Y diverge, Z it cannot judge". No LLM anywhere in this pipeline (gemma_review.py is optional and
separate).

Usage — straight off a save (needs nbtlib; reading is safe with the server up):
    python3 audit.py --region-dir World/DIM<id>/region --level level_live.dat \\
        --db recipes.sqlite --out-dir audits/<date> --label pos-fix

Usage — from a scan you already have (no nbtlib needed):
    python3 audit.py --patterns patterns.json.gz --idmaps idmaps.json.gz \\
        --db recipes.sqlite --out-dir audits/<date>

Writes into --out-dir:
    patterns.json.gz    the scan (scan_patterns.py format), always, so the directory is self-contained
    idmaps.json.gz      the id registry used (idmaps.py format)
    patterns_named.json string identities of every pattern (export_patterns.py format)
    patterns_named.txt  one line per pattern — the human-diffable "my world's recipes as strings"
    review.json         {meta, results}: verdict per pattern (review_matcher.py format)
    review.md           human-readable report, opens with the coverage statement
    summary.txt         the coverage statement alone, plain text (also printed to stdout)
    meta.json           inputs, sha256 of the recipe db, corpus label, timestamps, sha256 of the tools

Exit code is 0 whenever the audit ran: this is a report, not a gate (only a usage/IO error fails).
Compare two audit directories with diff_audits.py.
"""
import argparse
import datetime
import gzip
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import export_patterns
import review_matcher as rm

TOOLS = ('audit.py', 'review_matcher.py', 'export_patterns.py', 'scan_patterns.py', 'idmaps.py', 'diff_audits.py')
OUTPUTS = ('patterns.json.gz', 'idmaps.json.gz', 'patterns_named.json', 'patterns_named.txt',
           'review.json', 'review.md', 'summary.txt', 'meta.json')


def sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(chunk), b''):
            h.update(block)
    return h.hexdigest()


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


def dim_dir(region_dir):
    """Accepts `<dim>/region` (as documented) or `<dim>` itself; -> the dimension dir scan_patterns wants."""
    p = os.path.abspath(region_dir.rstrip('/'))
    if os.path.basename(p) == 'region':
        return os.path.dirname(p)
    if os.path.isdir(os.path.join(p, 'region')):
        return p
    raise SystemExit(f'--region-dir {region_dir!r}: expected a region/ directory (or a dim dir containing one)')


def write_gz_json(obj, path):
    with gzip.open(path, 'wt', encoding='utf-8') as f:
        json.dump(obj, f)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--region-dir', action='append', default=None, metavar='DIM/region',
                    help='region dir of a dimension to scan (repeatable); needs --level and nbtlib')
    ap.add_argument('--level', default=None, help='level.dat of that same world (its FML.ItemData is the id registry)')
    ap.add_argument('--players', default=None, help='playerdata dir to also scan (optional)')
    ap.add_argument('--patterns', default=None, help='instead of scanning: an existing scan_patterns.py output')
    ap.add_argument('--idmaps', default=None, help='instead of scanning: an existing idmaps.py output')
    ap.add_argument('--idmap-key', default='live', help='which idmaps label is the world registry (default: %(default)s)')
    ap.add_argument('--db', default='recipes.sqlite', help='recipes.sqlite (default: %(default)s, relative to the current directory)')
    ap.add_argument('--manifest', default=None, help='gtnh-rag data/raw/manifest.json (default: <dir of --db>/raw/manifest.json)')
    ap.add_argument('--out-dir', required=True, help='directory to create and fill with the audit')
    ap.add_argument('--force', action='store_true', help='allow writing into a non-empty existing --out-dir')
    ap.add_argument('--label', default=None, help='free text stored in meta (e.g. pos-fix)')
    a = ap.parse_args()

    scanning = bool(a.region_dir)
    if scanning == bool(a.patterns):
        ap.error('pass either --region-dir (+ --level) or --patterns (+ --idmaps), not both/neither')
    if scanning and not a.level:
        ap.error('--region-dir needs --level (the level.dat of that same world, for the id registry)')
    if not scanning and not a.idmaps:
        ap.error('--patterns needs --idmaps')
    if not os.path.exists(a.db):
        ap.error(f'--db not found: {a.db}')
    if os.path.isdir(a.out_dir) and os.listdir(a.out_dir) and not a.force:
        raise SystemExit(f'{a.out_dir} exists and is not empty; use --force to write into it anyway')
    os.makedirs(a.out_dir, exist_ok=True)

    def out(name):
        return os.path.join(a.out_dir, name)

    ts = {'started': now()}

    if scanning:
        import idmaps
        import scan_patterns
        dims = [dim_dir(r) for r in a.region_dir]
        print(f'scanning {len(dims)} dim(s): ' + ', '.join(dims), file=sys.stderr)
        pats = scan_patterns.scan(dims, a.players)
        maps = idmaps.build_maps([(a.idmap_key, a.level)])
    else:
        pats = rm.load_json(a.patterns)
        maps = rm.load_json(a.idmaps)
    ts['scan_done'] = now()
    write_gz_json(pats, out('patterns.json.gz'))
    write_gz_json(maps, out('idmaps.json.gz'))

    live = rm.live_ids(maps, a.idmap_key)
    print(f'{len(pats)} patterns, {len(live)} item ids in registry {a.idmap_key!r}; loading {a.db}', file=sys.stderr)
    rdb = rm.RecipeDB(a.db)
    ts['corpus_loaded'] = now()

    entries = export_patterns.build(pats, live, rdb)
    export_patterns.write_json(entries, out('patterns_named.json'))
    export_patterns.write_txt(entries, out('patterns_named.txt'))

    results = rm.review(pats, live, rdb, stacks=[{'in': e['in'], 'out': e['out']} for e in entries])
    corpus = rm.corpus_label(a.db, a.manifest)
    meta = rm.build_meta(results, out('patterns.json.gz'), out('idmaps.json.gz'), a.idmap_key, a.db, corpus, a.label)
    json.dump({'meta': meta, 'results': results}, open(out('review.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    rm.write_md(results, meta, out('review.md'))
    summary = rm.coverage_text(meta, results)
    open(out('summary.txt'), 'w', encoding='utf-8').write(summary + '\n')
    ts['finished'] = now()

    json.dump({
        'generated': ts['started'], 'label': a.label, 'mode': 'scan' if scanning else 'prescanned',
        'inputs': {'region_dirs': a.region_dir, 'level': a.level, 'players': a.players,
                   'patterns': a.patterns, 'idmaps': a.idmaps, 'idmap_key': a.idmap_key,
                   'db': os.path.abspath(a.db), 'manifest': a.manifest},
        'corpus': corpus, 'db_sha256': sha256(a.db), 'db_bytes': os.path.getsize(a.db),
        'patterns': len(pats), 'counts': meta['counts'], 'coverage': meta['coverage'],
        'timestamps': ts, 'outputs': list(OUTPUTS),
        'tool_sha256': {t: sha256(os.path.join(HERE, t)) for t in TOOLS if os.path.exists(os.path.join(HERE, t))},
    }, open(out('meta.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)

    print(summary)
    print(f'\naudit written to {a.out_dir}: ' + ', '.join(OUTPUTS), file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
