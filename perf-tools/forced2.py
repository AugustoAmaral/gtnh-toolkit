import nbtlib, glob, os, collections

base = os.environ.get("GTNH_WORLD", "path/to/World")
paths = [os.path.join(base, "forcedchunks.dat")] + sorted(glob.glob(os.path.join(base, "*", "forcedchunks.dat")))
grand = collections.Counter()
rows = []
for p in paths:
    dim = "DIM0 (overworld)" if os.path.dirname(p) == base else os.path.basename(os.path.dirname(p))
    try:
        f = nbtlib.load(p)
    except Exception as e:
        print(f"{dim}: ERRO {e}"); continue
    per_mod = collections.Counter()
    per_team = collections.Counter()
    for entry in f.get("TicketList", []):
        owner = str(entry.get("Owner", "?"))
        tks = entry.get("Tickets", [])
        per_mod[owner] += len(tks)
        for t in tks:
            md = t.get("ModData", {})
            per_team[f"{owner}:{md.get('Team','-')}"] += 1
        grand[owner] += len(tks)
    tot = sum(per_mod.values())
    if tot:
        rows.append((tot, dim, dict(per_mod), dict(per_team)))

rows.sort(reverse=True)
print("=== FORCED CHUNKS BY DIMENSION (persisted tickets) ===")
for tot, dim, per_mod, per_team in rows:
    print(f"{dim:<22} {tot:>6} tickets   {per_mod}   times={per_team}")
print(f"\n=== TOTAL BY MOD ===")
for m, c in grand.most_common():
    print(f"  {m:<24} {c}")
print(f"  {'GRAND TOTAL':<24} {sum(grand.values())}")
