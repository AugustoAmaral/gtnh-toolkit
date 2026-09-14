import sys, collections
path, root_needle = sys.argv[1], sys.argv[2]
maxdepth = int(sys.argv[3]) if len(sys.argv)>3 else 14
minpct = float(sys.argv[4]) if len(sys.argv)>4 else 0.4
stacks=[]; total=0
for line in open(path):
    line=line.rstrip("\n")
    if " " not in line: continue
    s,c=line.rsplit(" ",1)
    try: c=int(c)
    except ValueError: continue
    total+=c; stacks.append((s.split(";"),c))

sel=[]
for fr,c in stacks:
    idx=None
    for i,f in enumerate(fr):
        if root_needle in f: idx=i; break
    if idx is not None: sel.append((fr[idx:],c))
sub=sum(c for _,c in sel)
print(f"TOTAL={total}  |  subtree '{root_needle}' = {sub} ({100*sub/total:.2f}% of total)\n")

tree={}
for fr,c in sel:
    node=tree
    for f in fr[:maxdepth]:
        node=node.setdefault(f,{"__c":0,"__ch":{}}) if False else node
        # simpler
        break
# build properly
tree=collections.defaultdict(lambda: {"c":0,"ch":None})
def insert(d, frames, c):
    if not frames: return
    f=frames[0]
    n=d.setdefault(f,{"c":0,"ch":{}})
    n["c"]+=c
    insert(n["ch"], frames[1:], c)
root={}
for fr,c in sel: insert(root, fr[:maxdepth], c)

def show(d, depth=0):
    items=sorted(d.items(), key=lambda kv:-kv[1]["c"])
    for f,n in items:
        pct=100*n["c"]/total
        if pct < minpct: continue
        print(f"{'  '*depth}{pct:6.2f}%  {f[:110]}")
        show(n["ch"], depth+1)
show(root)
