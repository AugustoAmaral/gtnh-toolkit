import os, glob, zlib, gzip, io, struct, collections, sys
import nbtlib
base = os.environ.get("GTNH_WORLD", "path/to/World")

def read_region(path):
    out=[]
    with open(path,"rb") as f:
        header=f.read(4096)
        if len(header)<4096: return out
        for i in range(1024):
            off=int.from_bytes(header[i*4:i*4+3],"big"); cnt=header[i*4+3]
            if off==0: continue
            f.seek(off*4096)
            raw=f.read(cnt*4096)
            if len(raw)<5: continue
            length=int.from_bytes(raw[0:4],"big"); ctype=raw[4]
            data=raw[5:5+length-1]
            try:
                data = zlib.decompress(data) if ctype==2 else gzip.decompress(data)
            except Exception: continue
            try:
                nbt=nbtlib.File.parse(io.BytesIO(data))
            except Exception: continue
            out.append(nbt)
    return out

dims = sys.argv[1:] if len(sys.argv)>1 else ["", "DIM1", "DIM2", "DIM-1"]
for d in dims:
    rdir = os.path.join(base, d, "region") if d else os.path.join(base, "region")
    label = d or "DIM0 (overworld)"
    files = sorted(glob.glob(os.path.join(rdir,"*.mca")))
    ents=collections.Counter(); tes=collections.Counter(); nchunks=0
    chunk_ent=collections.Counter()
    for p in files:
        for nbt in read_region(p):
            lvl = nbt.get("Level")
            if lvl is None: continue
            nchunks+=1
            cx=int(lvl.get("xPos",0)); cz=int(lvl.get("zPos",0))
            el = lvl.get("Entities",[])
            for e in el:
                ents[str(e.get("id","?"))]+=1
            if el: chunk_ent[(cx,cz)]+=len(el)
            for t in lvl.get("TileEntities",[]):
                tes[str(t.get("id","?"))]+=1
    print(f"\n########## {label}: {nchunks} chunks saved, {sum(ents.values())} entities, {sum(tes.values())} TEs ##########")
    print("--- TOP 20 ENTITIES ---")
    for k,v in ents.most_common(20): print(f"   {v:>7}  {k}")
    print("--- TOP 10 CHUNKS WITH MOST ENTITIES (chunk coords / block) ---")
    for (cx,cz),v in chunk_ent.most_common(10): print(f"   {v:>6} ents  chunk({cx},{cz})  block({cx*16},{cz*16})")
    print("--- TOP 15 TILEENTITIES ---")
    for k,v in tes.most_common(15): print(f"   {v:>7}  {k}")
