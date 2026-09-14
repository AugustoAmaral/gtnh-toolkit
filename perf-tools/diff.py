import sys, collections
def load(p):
    st=[]; tot=0
    for line in open(p):
        line=line.rstrip("\n")
        if " " not in line: continue
        s,c=line.rsplit(" ",1)
        try: c=int(c)
        except ValueError: continue
        tot+=c; st.append((s.split(";"),c))
    return st,tot
A,ta=load(sys.argv[1]); C,tc=load(sys.argv[2])
print(f"A={ta} samples   C={tc} samples   delta={tc-ta:+d}\n")
def bucket(st, needle):
    return sum(c for fr,c in st if any(needle in f for f in fr))
buckets=["appeng/","TileLevelMaintainer","gregtech/api/metatileentity","BaseMetaPipeEntity",
 "crazypants/enderio","func_72939_s","func_72870_g","func_147456_g","ChunkProviderServer",
 "AnvilChunkLoader","libz","ServerThreadLongHashMap","EntityLiving","mods/railcraft",
 "ic2/","thaumcraft","cropsnh","func_71190_q","NibbleArray","Chunk.func_150806_e",
 "storagedrawers","MTEAdvEBF","binnie","forestry","GCTaskThread","JIT","func_147450_X"]
print(f"{'bucket':<42}{'A':>8}{'C':>8}{'delta':>9}")
rows=[]
for b in buckets:
    a=bucket(A,b); c=bucket(C,b); rows.append((c-a,b,a,c))
for d,b,a,c in sorted(rows, key=lambda r:-abs(r[0])):
    print(f"{b:<42}{a:>8}{c:>8}{d:>+9}")

print("\n=== TOP SELF: biggest INCREASES from A to C ===")
def selfmap(st):
    m=collections.Counter()
    for fr,c in st: m[fr[-1]]+=c
    return m
sa, sc = selfmap(A), selfmap(C)
delta=collections.Counter()
for k in set(sa)|set(sc): delta[k]=sc[k]-sa[k]
for k,d in delta.most_common(15): print(f"  {d:>+7}  {k[:110]}")
print("  ---- biggest DROPS ----")
for k,d in list(reversed(delta.most_common()))[:12]: print(f"  {d:>+7}  {k[:110]}")
