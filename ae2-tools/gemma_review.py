#!/usr/bin/env python3
"""Second opinion from Gemma 4 via OpenRouter (`omp -p --no-tools --no-session --model openrouter/google/gemma-4-31b-it`) on the
residue of review_matcher.py: every pattern the matcher rated MISMATCH or NO_RECIPE_FOUND, plus a CONTROL GROUP
of 40 patterns rated MATCH (the model is not told which are controls).  Controls the model calls DIFFERENT measure
its hallucination rate.  LLM verdicts never overwrite the matcher's: they are recorded side by side.

Optional and NOT part of the audit pipeline (audit.py never calls this): it was measured unreliable over the
reference dataset (6 of 9 "SAME" answers over the matcher's residue were hallucinated leniency).

Inputs: review_matcher.py --out-json, the `{"meta": ..., "results": [...]}` shape (see load_review()).
Outputs: gemma_prompts/batch_NN.txt (+ _try_T.txt raw responses), gemma_verdicts.json, and a
"Gemma review" section appended to the review .md.

Batches of at most 8 patterns per call; strict JSON answer; up to 3 tries per batch; leftovers -> LLM_FAILED.
"""
import argparse, collections, json, os, random, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
BATCH = 8
TRIES = 3
N_CONTROLS = 40
SEED = 20260912
MODEL = 'openrouter/google/gemma-4-31b-it'  # pinned to the Parasail provider in ~/.omp/agent/models.yml; avoid "gemma4" (ambiguous fuzzy match, used to hit Ollama Cloud)

INSTRUCTIONS = """You compare Applied Energistics 2 encoded patterns from a Minecraft GregTech modpack (GTNH) with candidate recipes taken from the pack's recipe database. Notation: "not consumed: X" = X is required but not consumed; "circuit N" = programmed circuit N; "(+N alt.)" = N other items are accepted in that slot; "mB" = millibuckets of fluid; "→" separates inputs from outputs.

For each PATTERN decide whether it is the SAME recipe as at least one of its CANDIDATE RECIPEs.
SAME = same set of input items/fluids and same outputs, where every amount in the pattern is the same integer multiple k of the candidate's amount (k may be 1). Ignore these differences: order; a programmed circuit missing from the pattern; a "not consumed" item missing from the pattern; a fluid missing from the pattern when the pattern lists no fluid at all; outputs of the candidate that the pattern omits.
DIFFERENT = an input item or fluid is missing, extra, or replaced by another one; an amount is not the same integer multiple as the others; the pattern output is not produced by the candidate or its amount is wrong; or there is no candidate at all.
UNSURE = you cannot decide.

Answer with ONLY a JSON array, no prose, no markdown fences, one object per PATTERN in the same order:
[{"pattern": <number>, "verdict": "SAME" | "DIFFERENT" | "UNSURE", "differences": "<short English text; empty string when SAME>"}]
"""


def run_omp(prompt_path, timeout=300):
    """Returns (stdout text, elapsed seconds). Runs from /tmp as required."""
    t0 = time.time()
    p = subprocess.run(['omp', '-p', '--no-tools', '--no-session', '--model', MODEL, '@' + prompt_path],
                       cwd='/tmp', capture_output=True, text=True, timeout=timeout)
    out = p.stdout
    # omp prints a "Working..." line before the answer
    out = re.sub(r'^\s*Working\.\.\.\s*\n?', '', out)
    return out, time.time() - t0, p.returncode, p.stderr


def parse_answer(text, expected):
    """Strict: a JSON array of objects with pattern in `expected`, verdict in the fixed set. Returns dict n->obj."""
    s = text.strip()
    if s.startswith('```'):
        s = re.sub(r'^```[a-zA-Z]*\s*', '', s)
        s = re.sub(r'\s*```\s*$', '', s)
    a, b = s.find('['), s.rfind(']')
    if a < 0 or b <= a:
        raise ValueError('no JSON array in answer')
    arr = json.loads(s[a:b + 1])
    if not isinstance(arr, list):
        raise ValueError('not a list')
    got = {}
    for o in arr:
        if not isinstance(o, dict):
            raise ValueError('element is not an object')
        n = o.get('pattern')
        if isinstance(n, str) and n.strip().isdigit():
            n = int(n)
        if not isinstance(n, int) or n not in expected:
            raise ValueError(f'unexpected pattern id {n!r}')
        v = str(o.get('verdict', '')).strip().upper()
        if v not in ('SAME', 'DIFFERENT', 'UNSURE'):
            raise ValueError(f'bad verdict {o.get("verdict")!r} for pattern {n}')
        got[n] = {'verdict': v, 'differences': str(o.get('differences', '') or '')}
    return got


def build_prompt(entries):
    lines = [INSTRUCTIONS]
    for r in entries:
        lines.append(f'PATTERN {r["index"]}: {r["pattern"]}')
        cands = r.get('top_candidates') or []
        if not cands and r.get('renamed_hint'):
            # NO_RECIPE_FOUND with a same-named item that has a recipe: show it, flagged
            for h in r['renamed_hint']:
                m = re.search(r'does have one \((.+?): (.+?)\) — ', h)
                if m:
                    lines.append(f'CANDIDATE RECIPE ({m.group(1)}; produces an item with the same name but a different id): {m.group(2)}')
        for c in cands[:3]:
            lines.append(f'CANDIDATE RECIPE ({c["machine"]}): {c["recipe_base"]}')
        if not cands and not r.get('renamed_hint'):
            lines.append('CANDIDATE RECIPE: none found in the database')
        lines.append('')
    return '\n'.join(lines)


def load_review(path):
    """review_matcher.py --out-json -> (meta, results). The file is `{"meta": {...}, "results": [...]}`;
    `results` is 1:1 with the scan order."""
    d = json.load(open(path, encoding='utf-8'))
    if not isinstance(d, dict) or 'results' not in d:
        raise SystemExit(f'{path}: expected the {{meta, results}} shape written by review_matcher.py')
    return d['meta'], d['results']


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--review', required=True, help='review_matcher.py --out-json output')
    ap.add_argument('--prompts', default='gemma_prompts', help='directory for prompt/response logs (default: %(default)s)')
    ap.add_argument('--out', default='gemma_verdicts.json')
    ap.add_argument('--md', required=True, help='review_all.md to append the "Gemma review" section to')
    ap.add_argument('--limit', type=int, default=0, help='debug: only the first N batches')
    ap.add_argument('--reuse', action='store_true',
                    help='keep verdicts already in --out whose matcher verdict is unchanged (controls included); '
                         'only query Gemma for residue patterns without a verdict; drop patterns that left the residue')
    a = ap.parse_args()
    os.makedirs(a.prompts, exist_ok=True)
    R = load_review(a.review)[1]
    residue = [r for r in R if r['verdict'] in ('MISMATCH', 'NO_RECIPE_FOUND')]
    matches = [r for r in R if r['verdict'] == 'MATCH']
    rng = random.Random(SEED)
    verdicts, log = {}, []
    byidx = {r['index']: r for r in R}
    if a.reuse and os.path.exists(a.out):
        prev = json.load(open(a.out, encoding='utf-8'))
        log = prev['log']
        for v in prev['verdicts']:
            r = byidx[v['index']]
            if v['matcher_verdict'] != r['verdict']:
                continue
            if v['group'] == 'control' or r['verdict'] in ('MISMATCH', 'NO_RECIPE_FOUND'):
                verdicts[v['index']] = v
        controls = [byidx[i] for i, v in verdicts.items() if v['group'] == 'control']
        residue = [r for r in residue if r['index'] not in verdicts]
        print(f'reuse: kept {len(verdicts)} verdicts ({len(controls)} controls); {len(residue)} residue patterns still to judge', file=sys.stderr)
    else:
        controls = rng.sample(matches, N_CONTROLS)
    group = {r['index']: 'residue' for r in residue}
    group.update({r['index']: 'control' for r in controls})
    items = residue + ([] if a.reuse and verdicts else controls)
    rng.shuffle(items)
    batches = [items[i:i + BATCH] for i in range(0, len(items), BATCH)]
    if a.limit:
        batches = batches[:a.limit]
    first_batch = 1 + max((l['batch'] for l in log), default=-1)
    print(f'{len(residue)} residue + {len(controls)} controls -> {len(batches)} batches', file=sys.stderr)

    for bi, batch in enumerate(batches, start=first_batch):
        expected = {r['index'] for r in batch}
        prompt = build_prompt(batch)
        ppath = os.path.join(a.prompts, f'batch_{bi:02d}.txt')
        open(ppath, 'w', encoding='utf-8').write(prompt)
        got = {}
        tries = 0
        for t in range(1, TRIES + 1):
            tries += 1
            out, dt, rc, err = run_omp(ppath)
            open(os.path.join(a.prompts, f'batch_{bi:02d}_try_{t}.txt'), 'w', encoding='utf-8').write(out)
            try:
                got = parse_answer(out, expected)
                missing = expected - set(got)
                if missing:
                    raise ValueError(f'missing patterns {sorted(missing)}')
                log.append({'batch': bi, 'try': t, 'ok': True, 'seconds': round(dt, 1)})
                break
            except Exception as e:
                log.append({'batch': bi, 'try': t, 'ok': False, 'seconds': round(dt, 1), 'error': str(e)[:200], 'rc': rc})
                print(f'batch {bi} try {t} failed: {e}', file=sys.stderr)
                got = got if isinstance(got, dict) else {}
        for r in batch:
            g = got.get(r['index'])
            verdicts[r['index']] = {
                'index': r['index'], 'group': group[r['index']], 'location': r['location'],
                'matcher_verdict': r['verdict'], 'gemma_verdict': g['verdict'] if g else 'LLM_FAILED',
                'gemma_differences': g['differences'] if g else '', 'batch': bi, 'tries': tries}
        print(f'batch {bi}: {sum(1 for r in batch if verdicts[r["index"]]["gemma_verdict"] != "LLM_FAILED")}/{len(batch)} after {tries} tries', file=sys.stderr)

    V = sorted(verdicts.values(), key=lambda v: v['index'])
    ctrl = [v for v in V if v['group'] == 'control']
    res = [v for v in V if v['group'] == 'residue']
    ctrl_c = collections.Counter(v['gemma_verdict'] for v in ctrl)
    res_c = collections.Counter((v['matcher_verdict'], v['gemma_verdict']) for v in res)
    ctrl_answered = [v for v in ctrl if v['gemma_verdict'] != 'LLM_FAILED']
    ctrl_wrong = [v for v in ctrl if v['gemma_verdict'] == 'DIFFERENT']
    n_batches = len({l['batch'] for l in log})
    n_calls = len(log)
    summary = {
        'model': MODEL, 'batch_size': BATCH, 'batches': n_batches, 'calls': n_calls,
        'retries': n_calls - n_batches,
        'failed_batches': sum(1 for b in {l['batch'] for l in log} if not any(l['ok'] for l in log if l['batch'] == b)),
        'llm_failed_patterns': sum(1 for v in V if v['gemma_verdict'] == 'LLM_FAILED'),
        'residue': len(res), 'controls': len(ctrl),
        'control_verdicts': dict(ctrl_c),
        'control_error_rate': (len(ctrl_wrong) / len(ctrl_answered)) if ctrl_answered else None,
        'residue_crosstab': {f'{m}|{g}': n for (m, g), n in sorted(res_c.items())},
    }
    json.dump({'summary': summary, 'log': log, 'verdicts': V}, open(a.out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    append_md(a.md, summary, V, R)


def append_md(md_path, S, V, R):
    byidx = {r['index']: r for r in R}
    L = ['', '## Gemma review (cheap LLM, second opinion — does NOT override the matcher)', '']
    L.append(f'Model `{S["model"]}` via `omp -p --no-tools --no-session --model {S["model"]} @prompt`; prompts and raw '
             f'responses in `gemma_prompts/`. Residue sent: {S["residue"]} patterns (`MISMATCH` + `NO_RECIPE_FOUND`), plus '
             f'{S["controls"]} controls (matcher `MATCH`, drawn with seed {SEED}; the model is not told which they are). '
             f'{S["batches"]} batches of up to {BATCH}, {S["calls"]} calls ({S["retries"]} retries), '
             f'{S["failed_batches"]} batch(es) with no valid answer, {S["llm_failed_patterns"]} pattern(s) `LLM_FAILED`.')
    L.append('')
    rate = S['control_error_rate']
    L.append('**Control group** (patterns the matcher calls `MATCH`, i.e. equal to the recipe): ' +
             ', '.join(f'`{k}` {n}' for k, n in sorted(S['control_verdicts'].items())) +
             (f'. Error rate (control called DIFFERENT): **{rate * 100:.1f}%**.' if rate is not None else '.'))
    L.append('')
    L.append('**Residue — matcher × Gemma:**')
    L.append('')
    L.append('| matcher | Gemma | patterns |\n|---|---|---|')
    for k, n in S['residue_crosstab'].items():
        m, g = k.split('|')
        L.append(f'| `{m}` | `{g}` | {n} |')
    L.append('')
    wrong_ctrl = [v for v in V if v['group'] == 'control' and v['gemma_verdict'] in ('DIFFERENT', 'UNSURE')]
    L.append(f'### Controls Gemma did NOT confirm ({len(wrong_ctrl)}) — model hallucination/uncertainty\n')
    for v in wrong_ctrl:
        r = byidx[v['index']]
        L.append(f'- **#{v["index"]}** — {v["location"]} — Gemma: `{v["gemma_verdict"]}` "{v["gemma_differences"]}"')
        L.append(f'  - Pattern: {r["pattern"]}')
        L.append(f'  - Recipe ({r["candidate_machine"]}): {r["candidate"]}')
    L.append('')
    same = [v for v in V if v['group'] == 'residue' and v['gemma_verdict'] == 'SAME']
    L.append(f'### Residue where Gemma says SAME and the matcher says `MISMATCH`/`NO_RECIPE_FOUND` ({len(same)}) — matcher false-positive candidates\n')
    L.append('Check them one by one; the matcher is literal (amount, exact item), Gemma may be ignoring a real difference.\n')
    for v in same:
        r = byidx[v['index']]
        L.append(f'- **#{v["index"]}** — {v["location"]} — matcher `{v["matcher_verdict"]}`: {"; ".join(r["diff"])}')
        L.append(f'  - Pattern: {r["pattern"]}')
        if r['candidate']:
            L.append(f'  - Best candidate ({r["candidate_machine"]}): {r["candidate"]}')
        if v['gemma_differences']:
            L.append(f'  - Gemma: "{v["gemma_differences"]}"')
    L.append('')
    uns = [v for v in V if v['group'] == 'residue' and v['gemma_verdict'] in ('UNSURE', 'LLM_FAILED')]
    L.append(f'### Residue with no Gemma verdict — UNSURE/LLM_FAILED ({len(uns)})\n')
    for v in uns:
        L.append(f'- #{v["index"]} — {v["location"]} — `{v["gemma_verdict"]}` "{v["gemma_differences"]}"')
    L.append('')
    diff = [v for v in V if v['group'] == 'residue' and v['gemma_verdict'] == 'DIFFERENT']
    L.append(f'### Residue confirmed DIFFERENT by Gemma ({len(diff)}) — agreement\n')
    for v in diff:
        L.append(f'- #{v["index"]} — {v["location"]} — Gemma: "{v["gemma_differences"]}"')
    L.append('')
    with open(md_path, 'a', encoding='utf-8') as f:
        f.write('\n'.join(L))


if __name__ == '__main__':
    main()
