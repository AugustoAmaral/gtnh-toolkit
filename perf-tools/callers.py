import sys, collections
path, needle = sys.argv[1], sys.argv[2]
stacks=[]; total=0
for line in open(path):
    line=line.rstrip("\n")
    if " " not in line: continue
    s,c=line.rsplit(" ",1)
    try: c=int(c)
    except ValueError: continue
    total+=c; stacks.append((s.split(";"),c))
callers=collections.Counter(); tot=0
for fr,c in stacks:
    for i,f in enumerate(fr):
        if needle in f:
            tot+=c
            # climb 1..4 frames looking for the first "mod/game" caller
            for j in range(i-1,-1,-1):
                p=fr[j]
                if not (p.startswith("java/") or "stub" in p or p.startswith("it/unimi")
                        or "ChunkProviderServer" in p or p.startswith("net/minecraft/world/World.func_72899_e")
                        or p.startswith("net/minecraft/world/World.func_72916_c")):
                    callers[p]+=c; break
            break
print(f"'{needle}' total = {tot} ({100*tot/total:.2f}%)  of {total} samples\n=== CALLERS ===")
for f,c in callers.most_common(20): print(f"{100*c/total:6.2f}%  {f[:120]}")
