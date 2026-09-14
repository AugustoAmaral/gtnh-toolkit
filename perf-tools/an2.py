import sys, collections
path = sys.argv[1]
stacks=[]; total=0
for line in open(path):
    line=line.rstrip("\n")
    if " " not in line: continue
    s,c=line.rsplit(" ",1)
    try: c=int(c)
    except ValueError: continue
    total+=c; stacks.append((s.split(";"),c))
print(f"TOTAL={total}\n")

# 1) stack roots (thread entry)
roots=collections.Counter()
for fr,c in stacks: roots[fr[0]]+=c
print("=== STACK ROOTS (top 12) ===")
for f,c in roots.most_common(12): print(f"{100*c/total:6.2f}%  {f[:110]}")

# 2) buckets with SRG names
buckets=[
 ("World.func_72939_s  updateEntities (TEs + entities)","func_72939_s"),
 ("WorldServer.func_147456_g  random block ticks","func_147456_g"),
 ("ChunkProviderServer.func_73156_b  unload/load queue","func_73156_b"),
 ("ChunkProviderServer (any)","ChunkProviderServer"),
 ("AnvilChunkLoader (save/read chunk)","AnvilChunkLoader"),
 ("libz (chunk compression)","libz"),
 ("MinecraftServer.func_71190_q  updateTimeLightAndEntities","func_71190_q"),
 ("GregTech TileEntity","gregtech/api/metatileentity"),
 ("GregTech pipes/cabos","BaseMetaPipeEntity"),
 ("EnderIO conduits","crazypants/enderio/conduit"),
 ("AE2","appeng/"),
 ("OpenComputers","li/cil/oc"),
 ("Railcraft","mods/railcraft"),
 ("Thaumcraft","thaumcraft/"),
 ("LogisticsPipes","logisticspipes/"),
 ("IC2","ic2/"),
 ("Crops (CropsNH/IC2 crops)","crop"),
]
print("\n=== STACKS CONTAINING (inclusive, may sum to >100%) ===")
for name,needle in buckets:
    c=sum(cnt for fr,cnt in stacks if any(needle in f for f in fr))
    if c: print(f"{100*c/total:6.2f}%  {c:>7}  {name}")

print("\n=== HOT LEAF CALLERS ===")
for leaf_needle in ["Long2ObjectOpenHashMap.containsKey","ObjectOpenHashSet.get","HashMap.getNode","BlockLookupHooks.getBlockById"]:
    callers=collections.Counter(); tot=0
    for fr,c in stacks:
        if leaf_needle in fr[-1]:
            tot+=c
            # climb until a non-fastutil/non-jdk frame is found
            for f in reversed(fr[:-1]):
                if not (f.startswith("it/unimi") or f.startswith("java/") or "stub" in f):
                    callers[f]+=c; break
    print(f"\n--- {leaf_needle}  ({100*tot/total:.2f}% of total) ---")
    for f,c in callers.most_common(8): print(f"   {100*c/total:6.2f}%  {f[:120]}")
