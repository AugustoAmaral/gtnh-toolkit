import os, glob, zlib, gzip, io, collections, sys, math
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
            f.seek(off*4096); raw=f.read(cnt*4096)
            if len(raw)<5: continue
            length=int.from_bytes(raw[0:4],"big"); ctype=raw[4]
            data=raw[5:5+length-1]
            try: data = zlib.decompress(data) if ctype==2 else gzip.decompress(data)
            except Exception: continue
            try: out.append(nbtlib.File.parse(io.BytesIO(data)))
            except Exception: pass
    return out

 # targets: chunks with high entity counts
alvos = [] # Edit this list with (cx, cz) coordinates
regs = set((cx>>5, cz>>5) for cx,cz in alvos)
alvo_set=set(alvos)
for rx,rz in sorted(regs):
    p=os.path.join(base,"region",f"r.{rx}.{rz}.mca")
    if not os.path.exists(p): continue
    for nbt in read_region(p):
        lvl=nbt.get("Level")
        if lvl is None: continue
        cx,cz=int(lvl.get("xPos",0)),int(lvl.get("zPos",0))
        if (cx,cz) not in alvo_set: continue
        ents=lvl.get("Entities",[])
        if not ents: continue
        print(f"\n########## chunk ({cx},{cz})  =  blocks X {cx*16}..{cx*16+15}, Z {cz*16}..{cz*16+15}  —  {len(ents)} entities ##########")
        por_tipo=collections.Counter(); ys=collections.Counter(); amostra={}
        for e in ents:
            t=str(e.get("id","?")); por_tipo[t]+=1
            pos=e.get("Pos",[0,0,0])
            try: y=int(float(pos[1])); x=int(float(pos[0])); z=int(float(pos[2]))
            except Exception: y=x=z=0
            ys[y//16*16]+=1
            if t not in amostra: amostra[t]=(x,y,z)
        for t,n in por_tipo.most_common(15):
            x,y,z=amostra[t]
            print(f"   {n:>4}  {t:<34} ex.: ({x},{y},{z})")
        print(f"   -- Y ranges: {dict(sorted(ys.items()))}")
        tes=lvl.get("TileEntities",[])
        if tes:
            tt=collections.Counter(str(t.get('id','?')) for t in tes)
            print(f"   -- TEs in chunk ({len(tes)}): {dict(tt.most_common(6))}")
