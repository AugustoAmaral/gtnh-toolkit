#!/usr/bin/env python3
"""Closed-loop balance of LFTR Fuel 3 (GTNH 2.9).

Computes external inputs, surpluses, and machine counts for a given
number of reactors, reading recipes from the database.
"""
import sqlite3, sys, os, argparse

parser = argparse.ArgumentParser()
parser.add_argument("--db", help="Path to recipes.sqlite")
parser.add_argument("reactors", type=int, nargs="?", default=16, help="Number of reactors")
args = parser.parse_args()

db_path = args.db or os.environ.get('GTNH_RECIPES_DB') or './recipes.sqlite'
if not os.path.exists(db_path):
    print(f"Error: Database file not found at {db_path}", file=sys.stderr)
    sys.exit(1)

DB = db_path
N = args.reactors

# ---- ASSUMED (wiki "Sparge Tower > Usage"; the dump stores these outputs as 0) ----
SPARGE_TB_HI = 20     # ThF4/LiF/NpF6/TcF6/SeF6: 1-20 L cada por op
SPARGE_U_HI = 200     # Xe/Ne/Ar/Kr/Rn: 1-200 L cada por op
SPARGE_N_BYPRODUCTS = 5
def avg(hi): return (1 + hi) / 2

c = sqlite3.connect(DB); c.row_factory = sqlite3.Row

def recipe(type_like, must_out=None, must_in=None):
    """Finds 1 recipe by type + mandatory fluids. Fails if not unique."""
    rows = c.execute("""select r.ID from RECIPE r join RECIPE_TYPE rt on rt.ID=r.RECIPE_TYPE_ID
                        where rt.TYPE like ?""", (type_like,)).fetchall()
    found = []
    for r in rows:
        d = detail(r['ID'])
        if must_out and not all(k in d['fout'] for k in must_out): continue
        if must_in and not all(k in d['fin'] or k in d['iin'] for k in must_in): continue
        found.append(d)
    assert len(found) == 1, f"{type_like} {must_out} {must_in} -> {len(found)} recipes"
    return found[0]

def detail(rid):
    g = c.execute("select VOLTAGE,VOLTAGE_TIER,DURATION from GREG_TECH_RECIPE where RECIPE_ID=?", (rid,)).fetchone()
    # IMPORTANT: a recipe may list the SAME fluid on two rows (the LFTR outputs
    # 20 mB + 10 mB of UF6). Sum them, never overwrite.
    def acc(rows):
        d = {}
        for r in rows: d[r['n']] = d.get(r['n'], 0) + r['a']
        return d
    fin = acc(c.execute("""select f.LOCALIZED_NAME n, fs.FLUID_STACKS_AMOUNT a
        from RECIPE_FLUID_GROUP rfg join FLUID_GROUP_FLUID_STACKS fs on fs.FLUID_GROUP_ID=rfg.FLUID_INPUTS_ID
        join FLUID f on f.ID=fs.FLUID_STACKS_FLUID_ID where rfg.RECIPE_ID=?""", (rid,)))
    fout = acc(c.execute("""select f.LOCALIZED_NAME n, o.FLUID_OUTPUTS_VALUE_AMOUNT a
        from RECIPE_FLUID_OUTPUTS o join FLUID f on f.ID=o.FLUID_OUTPUTS_VALUE_FLUID_ID
        where o.RECIPE_ID=?""", (rid,)))
    iin = acc(c.execute("""select i.LOCALIZED_NAME n, gs.ITEM_STACKS_STACK_SIZE a
        from RECIPE_ITEM_GROUP rig join ITEM_GROUP_ITEM_STACKS gs on gs.ITEM_GROUP_ID=rig.ITEM_INPUTS_ID
        join ITEM i on i.ID=gs.ITEM_STACKS_ITEM_ID where rig.RECIPE_ID=?""", (rid,)))
    iout = {}
    for r in c.execute("""select i.LOCALIZED_NAME n, o.ITEM_OUTPUTS_VALUE_STACK_SIZE a, o.ITEM_OUTPUTS_VALUE_PROBABILITY p
        from RECIPE_ITEM_OUTPUTS o join ITEM i on i.ID=o.ITEM_OUTPUTS_VALUE_ITEM_ID where o.RECIPE_ID=?""", (rid,)):
        iout[r['n']] = iout.get(r['n'], 0) + r['a'] * r['p']
    return dict(id=rid, eut=g['VOLTAGE'] if g else 0, tier=g['VOLTAGE_TIER'] if g else '-',
                dur=(g['DURATION'] / 20 if g else 0), fin=fin, fout=fout, iin=iin, iout=iout)

# ---------------- cycle recipes ----------------
R = {}
R['lftr']      = recipe('Liquid Fluoride Thorium Reactor%', must_in=['LFTR Fuel 3'])
R['rfpp']      = recipe('Nuclear Fuel Processing%', must_in=['LFTR Fuel Base'], must_out=['LFTR Fuel 3'])
R['chemplant'] = recipe('Chemical Plant%', must_out=['LFTR Fuel Base'])
R['dt']        = recipe('Distillation Tower%', must_out=['Stable Molten Salt Base'])
R['nspp_u']    = recipe('Nuclear Salt Processing Plant%', must_in=['Helium Sparged U Salt'])
R['nspp_tb']   = recipe('Nuclear Salt Processing Plant%', must_in=['Fluorine Sparged TB Salt'])
R['sparge_he'] = recipe('LFTR Gas Sparging%', must_in=['Uranium Depleted Molten Salt (U Salt)'])
R['sparge_f']  = recipe('LFTR Gas Sparging%', must_in=['Thorium-Beryllium Depleted Molten Salt (TB Salt)'])
R['ebf_lftb']  = recipe('Blast Furnace%', must_in=['Lithium Tetrafluoroberyllate'] if False else None,
                        must_out=None) if False else detail(
    c.execute("""select r.ID from RECIPE r join RECIPE_TYPE rt on rt.ID=r.RECIPE_TYPE_ID
                 join RECIPE_ITEM_OUTPUTS o on o.RECIPE_ID=r.ID join ITEM i on i.ID=o.ITEM_OUTPUTS_VALUE_ITEM_ID
                 where rt.TYPE like 'Blast Furnace%' and i.LOCALIZED_NAME='Lithium Tetrafluoroberyllate Fuel Compound Dust'""").fetchone()[0])
R['fe_lftb']   = detail(c.execute("""select r.ID from RECIPE r join RECIPE_TYPE rt on rt.ID=r.RECIPE_TYPE_ID
                 join RECIPE_FLUID_OUTPUTS o on o.RECIPE_ID=r.ID join FLUID f on f.ID=o.FLUID_OUTPUTS_VALUE_FLUID_ID
                 where rt.TYPE like 'Fluid Extractor (HV)%' and f.LOCALIZED_NAME like 'Lithium Tetrafluoroberyllate (LFTB)%'""").fetchone()[0])
R['lif']       = recipe('Chemical Bath%', must_in=['Lithium Carbonate Dust'])
R['bef2']      = recipe('Multiblock Dehydrator (MV)%', must_in=['Ammonium Tetrafluoroberyllate'])
R['atfb']      = recipe('Multiblock Dehydrator (MV)%', must_out=['Ammonium Tetrafluoroberyllate'])
R['abf']       = recipe('Large Chemical Reactor (LV)%', must_out=['Ammonium Bifluoride'],
                        must_in=['Industrial Strength Hydrofluoric Acid'])
R['beoh']      = recipe('Large Chemical Reactor (LV)%', must_out=['Beryllium Hydroxide'])
R['hf']        = recipe('Large Chemical Reactor (ULV)%', must_out=['Hydrofluoric Acid'], must_in=['Fluorine'])
R['ishf']      = recipe('Chemical Plant (HV)%', must_out=['Industrial Strength Hydrofluoric Acid'])
R['f_electro'] = recipe('Multiblock Electrolyzer (MV)%', must_in=['Fluorspar Dust'])

DUST_MB = 144  # 1 dust = 144 mB (padrao GT, confirmado no Fluid Extractor de LiF/BeF2/LFTB)

# ---------------- flows ----------------
flow = {}          # mB/s (positivo = produzido, negativo = consumido)
def add(name, rate): flow[name] = flow.get(name, 0.0) + rate

def run(r, ops_per_s, label=None):
    """Runs a recipe at `ops_per_s` operations per second and accounts for everything."""
    for k, v in r['fin'].items():  add(k, -v * ops_per_s)
    for k, v in r['fout'].items(): add(k, +v * ops_per_s)
    for k, v in r['iin'].items():
        if v: add('[item] ' + k, -v * ops_per_s)
    for k, v in r['iout'].items(): add('[item] ' + k, +v * ops_per_s)

# 1) reactors
lftr = R['lftr']
ops_lftr = N / lftr['dur']
run(lftr, ops_lftr)
fuel3_need = lftr['fin']['LFTR Fuel 3'] * ops_lftr

# 2) RFPP: produces the consumed Fuel 3
run(R['rfpp'], fuel3_need / R['rfpp']['fout']['LFTR Fuel 3'])
fb_need = -flow['LFTR Fuel Base']

# 3) Chem Plant: produces the Fuel Base
run(R['chemplant'], fb_need / R['chemplant']['fout']['LFTR Fuel Base'])
stable_need = -flow['Stable Molten Salt Base']

# 4) Dangote/DT: produces the Stable
run(R['dt'], stable_need / R['dt']['fout']['Stable Molten Salt Base'])
impure_need = -flow['Impure Molten Salt Base']

# 5) NSPP U-branch: produces the Impure
run(R['nspp_u'], impure_need / R['nspp_u']['fout']['Impure Molten Salt Base'])
usalt_sparged_need = -flow['Helium Sparged U Salt']

# 6) Helium sparging: sparge 100% of U-Salt, not just what's necessary
sp_he = R['sparge_he']
usalt_avail = flow['Uranium Depleted Molten Salt (U Salt)']
ops_he = usalt_avail / sp_he['fin']['Uranium Depleted Molten Salt (U Salt)']
run(sp_he, ops_he)
nobles = ['Xenon', 'Neon', 'Argon', 'Krypton', 'Radon']
for g in nobles: add(g, avg(SPARGE_U_HI) * ops_he)
he_returned = (sp_he['fin']['Helium'] - SPARGE_N_BYPRODUCTS * avg(SPARGE_U_HI)) * ops_he
add('Helium', he_returned)   # camada 3: restante volta pra hatch de entrada

# 7) Fluorine sparging: 100% of TB-Salt
sp_f = R['sparge_f']
tb_avail = flow['Thorium-Beryllium Depleted Molten Salt (TB Salt)']
ops_f = tb_avail / sp_f['fin']['Thorium-Beryllium Depleted Molten Salt (TB Salt)']
run(sp_f, ops_f)
for g in ['Thorium Tetrafluoride', 'Lithium Fluoride', 'Neptunium Hexafluoride',
          'Technetium Hexafluoride', 'Selenium Hexafluoride']:
    add(g, avg(SPARGE_TB_HI) * ops_f)
f_returned = (sp_f['fin']['Fluorine'] - SPARGE_N_BYPRODUCTS * avg(SPARGE_TB_HI)) * ops_f
add('Fluorine', f_returned)

# 8) NSPP TB-branch: processes ALL sparged TB-Salt
run(R['nspp_tb'], flow['Fluorine Sparged TB Salt'] / R['nspp_tb']['fin']['Fluorine Sparged TB Salt'])

# 9) LFTB: Fluid Extractor -> EBF -> LiF and BeF2 dusts
lftb_need = -flow['Lithium Tetrafluoroberyllate (LFTB)']
run(R['fe_lftb'], lftb_need / R['fe_lftb']['fout']['Lithium Tetrafluoroberyllate (LFTB)'])
lftb_dust = -flow['[item] Lithium Tetrafluoroberyllate Fuel Compound Dust']
run(R['ebf_lftb'], lftb_dust / R['ebf_lftb']['iout']['Lithium Tetrafluoroberyllate Fuel Compound Dust'])

# 10) LiF/BeF2 recycled fluid -> dust (dehydrator, 144 mB -> 1 dust)
for fluido, dust in [('Lithium Fluoride', 'Lithium Fluoride Dust'),
                     ('Beryllium Fluoride', 'Beryllium Fluoride Dust')]:
    disponivel = max(flow.get(fluido, 0.0), 0.0)
    convertido = min(disponivel, -flow['[item] ' + dust] * DUST_MB)
    add(fluido, -convertido); add('[item] ' + dust, convertido / DUST_MB)

# 11) LiF deficit -> Chemical Bath (Li2CO3 + ISHF)
lif_falta = -flow['[item] Lithium Fluoride Dust']
if lif_falta > 0: run(R['lif'], lif_falta / R['lif']['iout']['Lithium Fluoride Dust'])

# 12) BeF2 deficit -> ATFB -> ABF -> Be(OH)2
bef2_falta = -flow['[item] Beryllium Fluoride Dust']
if bef2_falta > 0:
    run(R['bef2'], bef2_falta / R['bef2']['iout']['Beryllium Fluoride Dust'])
    run(R['atfb'], -flow['Ammonium Tetrafluoroberyllate'] / R['atfb']['fout']['Ammonium Tetrafluoroberyllate'])
    run(R['abf'], -flow['Ammonium Bifluoride'] / R['abf']['fout']['Ammonium Bifluoride'])
    beoh_dust = -flow['[item] Beryllium Hydroxide Dust']
    run(R['beoh'], beoh_dust * DUST_MB / R['beoh']['fout']['Beryllium Hydroxide'])
    add('Beryllium Hydroxide', -beoh_dust * DUST_MB); add('[item] Beryllium Hydroxide Dust', beoh_dust)

# 13) Missing ISHF -> Chem Plant HV (2000 ISHF + 5000 HF -> 4500 ISHF) -> HF from fluorine
ishf_falta = -flow['Industrial Strength Hydrofluoric Acid']
if ishf_falta > 0:
    r = R['ishf']
    liq = r['fout']['Industrial Strength Hydrofluoric Acid'] - r['fin']['Industrial Strength Hydrofluoric Acid']
    run(r, ishf_falta / liq)
hf_falta = -flow['Hydrofluoric Acid']
if hf_falta > 0: run(R['hf'], hf_falta / R['hf']['fout']['Hydrofluoric Acid'])

# 14) Missing fluorine -> fluorspar electrolysis
f_falta = -flow['Fluorine']
if f_falta > 0: run(R['f_electro'], f_falta / R['f_electro']['fout']['Fluorine'])

# ---------------- report ----------------
def fmt(v): return f"{v:>10.2f}"
print(f"=== Fuel 3 cycle balance -- {N} reactors (steady state, no cold trap, 100% sparge)\n")
print("--- Recipes used (from recipes.sqlite)")
for k, r in R.items():
    print(f"  {k:<10} {r['tier']:>4} {r['eut']:>6} EU/t {r['dur']:>7.1f}s  "
          f"IN {dict(list(r['fin'].items())+[(i,a) for i,a in r['iin'].items() if a])} -> "
          f"OUT {dict(list(r['fout'].items())+list(r['iout'].items()))}")

ext = {k: -v for k, v in flow.items() if v < -1e-9}
sob = {k: v for k, v in flow.items() if v > 1e-9}
print(f"\n--- EXTERNAL INPUTS (mB/s, or dust/s in [item])")
for k in sorted(ext, key=lambda x: -ext[x]):
    extra = f"   = {ext[k]*60:.1f} dust/min" if k.startswith('[item]') else f"   = {ext[k]*3600/1000:.0f} k mB/h"
    print(f"  {k:<52}{fmt(ext[k])}{extra}")
print(f"\n--- SURPLUS / VOID (mB/s, or dust/s in [item])")
for k in sorted(sob, key=lambda x: -sob[x]):
    extra = f"   = {sob[k]*60:.1f} dust/min" if k.startswith('[item]') else ""
    print(f"  {k:<52}{fmt(sob[k])}{extra}")

print(f"\n--- Checks")
print(f"  Fuel 3 consumed by reactors          {fmt(fuel3_need)} mB/s")
print(f"  Fuel Base needed                     {fmt(fb_need)} mB/s")
print(f"  Stable Base needed                   {fmt(stable_need)} mB/s")
print(f"  Impure needed                        {fmt(impure_need)} mB/s")
print(f"  He-Sparged U-Salt used by NSPP        {fmt(usalt_sparged_need)} mB/s  (of {usalt_avail:.2f} produced)")
print(f"  ops/s sparge He {ops_he:.4f}   ops/s sparge F {ops_f:.4f}")

# ---------------- outside the recipe system ----------------
# Wiki "Liquid Fluoride Thorium Reactor > Usage": 1/300 chance per tick of 1-10 L of
# Molten U-233. Hardcoded in the controller, does not appear in recipe map or NEI.
U233 = N * (1/300) * ((1+10)/2) * 20   # mB/s  (20 ticks/s)

# ---------------- sizing ----------------
TIER_EU = dict(LV=32, MV=128, HV=512, EV=2048, IV=8192, LuV=32768, ZPM=131072, UV=524288)

def throughput(r, hatch, speed=1.0, max_par=1, eut_mult=1.0):
    """mB(ou dust)/s de UMA maquina. Regra GT: maximiza parallels com a energia
    disponivel, depois overclocka o lote (imperfect OC: 4x EU, 2x speed)."""
    eu_hatch = TIER_EU[hatch]; eut = r['eut'] * eut_mult
    par = max(1, min(max_par, int(eu_hatch // eut) if eut else max_par))
    oc = 0
    while eu_hatch >= par * eut * (4 ** (oc + 1)): oc += 1
    dur = r['dur'] / speed / (2 ** oc)
    return par / dur, par, oc, dur

def linha(nome, r, hatch, need, key, speed=1.0, max_par=1, eut_mult=1.0):
    ops, par, oc, dur = throughput(r, hatch, speed, max_par, eut_mult)
    por_maquina = ops * (r['fout'].get(key) or r['iout'].get(key) or r['fin'].get(key) or 0)
    print(f"  {nome:<34} hatch {hatch:<4} par={par:<3} oc={oc} ciclo={dur:>6.1f}s  "
          f"{por_maquina:>8.2f}/s per machine  needs {need:>7.2f}  ->  {need/por_maquina:>5.2f} machines")

print(f"\n--- Outside the recipe map")
print(f"  Molten Uranium 233 (bonus 1/300 per tick)  {U233:>9.2f} mB/s  -> accumulates, does not block the reactor")

print(f"\n--- Sizing (1 machine each, at the indicated hatch)")
linha("RFPP (Fuel 3)",        R['rfpp'],      'LuV', fuel3_need,   'LFTR Fuel 3')
linha("Chem Plant (Fuel Base)", R['chemplant'],'IV', fb_need,      'LFTR Fuel Base')
linha("Dangote DT (Stable)",  R['dt'],        'LuV', stable_need,  'Stable Molten Salt Base', speed=3.0, max_par=12)
linha("Dangote DT (Stable)",  R['dt'],        'IV',  stable_need,  'Stable Molten Salt Base', speed=3.0, max_par=12)
linha("NSPP U-Salt (circ 1)", R['nspp_u'],    'IV',  usalt_sparged_need, 'Helium Sparged U Salt', speed=2.5, max_par=10)
linha("NSPP U-Salt (circ 1)", R['nspp_u'],    'LuV', usalt_sparged_need, 'Helium Sparged U Salt', speed=2.5, max_par=12)
linha("NSPP TB-Salt (circ 8)",R['nspp_tb'],   'IV',  tb_avail,     'Fluorine Sparged TB Salt', speed=2.5, max_par=10)
linha("Sparge He",            R['sparge_he'], 'IV',  usalt_avail,  'Uranium Depleted Molten Salt (U Salt)')
linha("Sparge F",             R['sparge_f'],  'IV',  tb_avail,     'Thorium-Beryllium Depleted Molten Salt (TB Salt)')
linha("Volcanus (LFTB dust)", R['ebf_lftb'],  'IV',  lftb_dust,    'Lithium Tetrafluoroberyllate Fuel Compound Dust',
      speed=1.2, max_par=8, eut_mult=0.9)
linha("Fluid Extractor (LFTB)",R['fe_lftb'],  'HV',  lftb_need,    'Lithium Tetrafluoroberyllate (LFTB)')
print("  -- same machines with more power:")
linha("Chem Plant (4 hatches IV=32k)", R['chemplant'], 'LuV', fb_need, 'LFTR Fuel Base')
linha("Volcanus (LuV ou 2x IV)", R['ebf_lftb'], 'LuV', lftb_dust,
      'Lithium Tetrafluoroberyllate Fuel Compound Dust', speed=1.2, max_par=8, eut_mult=0.9)
linha("Sparge F (hatch LuV)", R['sparge_f'], 'LuV', tb_avail, 'Thorium-Beryllium Depleted Molten Salt (TB Salt)')
