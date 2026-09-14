"""Exact recipe lookup over data/recipes.sqlite (imported nesql dump)."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


@dataclass
class Recipe:
    recipe_id: str
    type_name: str
    inputs: list[str] = field(default_factory=list)
    fluid_inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    fluid_outputs: list[str] = field(default_factory=list)
    eu_t: int | None = None
    tier: str | None = None
    amps: int | None = None
    duration_ticks: int | None = None
    info: str = ""


def find_items(conn: sqlite3.Connection, name: str) -> list[tuple[str, str]]:
    exact = conn.execute(
        "SELECT ID, LOCALIZED_NAME FROM ITEM WHERE LOWER(LOCALIZED_NAME)=LOWER(?) ORDER BY ID LIMIT 20", (name,)
    ).fetchall()
    if len(exact) >= 20:
        return exact
    partial = conn.execute(
        "SELECT ID, LOCALIZED_NAME FROM ITEM WHERE LOWER(LOCALIZED_NAME) LIKE LOWER(?) "
        "AND LOWER(LOCALIZED_NAME)<>LOWER(?) ORDER BY LENGTH(LOCALIZED_NAME), ID LIMIT ?",
        (f"%{name}%", name, 20 - len(exact)),
    ).fetchall()
    return exact + partial


def item_group_label(conn: sqlite3.Connection, group_id: str) -> str:
    rows = conn.execute(
        "SELECT s.ITEM_STACKS_STACK_SIZE, i.LOCALIZED_NAME FROM ITEM_GROUP_ITEM_STACKS s "
        "JOIN ITEM i ON i.ID = s.ITEM_STACKS_ITEM_ID WHERE s.ITEM_GROUP_ID=? ORDER BY i.LOCALIZED_NAME",
        (group_id,),
    ).fetchall()
    if not rows:
        return "?"
    size = rows[0][0]
    names = sorted({r[1] for r in rows})
    label = " / ".join(names[:3]) + (f" / +{len(names) - 3}" if len(names) > 3 else "")
    return f"{size}x {label}"


def _fluid_group_label(conn: sqlite3.Connection, group_id: str) -> str:
    rows = conn.execute(
        "SELECT s.FLUID_STACKS_AMOUNT, f.LOCALIZED_NAME FROM FLUID_GROUP_FLUID_STACKS s "
        "JOIN FLUID f ON f.ID=s.FLUID_STACKS_FLUID_ID WHERE s.FLUID_GROUP_ID=? ORDER BY f.LOCALIZED_NAME",
        (group_id,),
    ).fetchall()
    if not rows:
        return "?"
    amount = rows[0][0]
    names = sorted({r[1] for r in rows})
    label = " / ".join(names[:3]) + (f" / +{len(names) - 3}" if len(names) > 3 else "")
    return f"{amount} mB {label}"


def load_recipe(conn: sqlite3.Connection, recipe_id: str) -> Recipe:
    type_name = conn.execute(
        "SELECT t.TYPE FROM RECIPE r JOIN RECIPE_TYPE t ON t.ID=r.RECIPE_TYPE_ID WHERE r.ID=?", (recipe_id,)
    ).fetchone()
    rec = Recipe(recipe_id=recipe_id, type_name=type_name[0] if type_name else "?")
    for (group_id,) in conn.execute(
        "SELECT ITEM_INPUTS_ID FROM RECIPE_ITEM_GROUP WHERE RECIPE_ID=? ORDER BY ITEM_INPUTS_KEY", (recipe_id,)
    ):
        rec.inputs.append(item_group_label(conn, group_id))
    for (group_id,) in conn.execute(
        "SELECT FLUID_INPUTS_ID FROM RECIPE_FLUID_GROUP WHERE RECIPE_ID=? ORDER BY FLUID_INPUTS_KEY", (recipe_id,)
    ):
        rec.fluid_inputs.append(_fluid_group_label(conn, group_id))
    for size, prob, name in conn.execute(
        "SELECT o.ITEM_OUTPUTS_VALUE_STACK_SIZE, o.ITEM_OUTPUTS_VALUE_PROBABILITY, i.LOCALIZED_NAME "
        "FROM RECIPE_ITEM_OUTPUTS o JOIN ITEM i ON i.ID=o.ITEM_OUTPUTS_VALUE_ITEM_ID "
        "WHERE o.RECIPE_ID=? ORDER BY o.ITEM_OUTPUTS_KEY",
        (recipe_id,),
    ):
        suffix = "" if prob >= 1.0 else f" @{prob * 100:g}%"
        rec.outputs.append(f"{size}x {name}{suffix}")
    for amount, prob, name in conn.execute(
        "SELECT o.FLUID_OUTPUTS_VALUE_AMOUNT, o.FLUID_OUTPUTS_VALUE_PROBABILITY, f.LOCALIZED_NAME "
        "FROM RECIPE_FLUID_OUTPUTS o JOIN FLUID f ON f.ID=o.FLUID_OUTPUTS_VALUE_FLUID_ID "
        "WHERE o.RECIPE_ID=? ORDER BY o.FLUID_OUTPUTS_KEY",
        (recipe_id,),
    ):
        suffix = "" if prob >= 1.0 else f" @{prob * 100:g}%"
        rec.fluid_outputs.append(f"{amount} mB {name}{suffix}")
    gt = conn.execute(
        "SELECT VOLTAGE, VOLTAGE_TIER, AMPERAGE, DURATION, ADDITIONAL_INFO FROM GREG_TECH_RECIPE WHERE RECIPE_ID=?",
        (recipe_id,),
    ).fetchone()
    if gt:
        rec.eu_t, rec.tier, rec.amps, rec.duration_ticks, rec.info = gt[0], gt[1], gt[2], gt[3], gt[4] or ""
    return rec


def recipes_producing(conn: sqlite3.Connection, item_id: str, limit: int = 200) -> list[Recipe]:
    ids = [r[0] for r in conn.execute(
        "SELECT DISTINCT RECIPE_ID FROM RECIPE_ITEM_OUTPUTS WHERE ITEM_OUTPUTS_VALUE_ITEM_ID=? ORDER BY RECIPE_ID LIMIT ?",
        (item_id, limit),
    )]
    return [load_recipe(conn, i) for i in ids]


def recipes_using(conn: sqlite3.Connection, item_id: str, limit: int = 200) -> list[Recipe]:
    ids = [r[0] for r in conn.execute(
        "SELECT DISTINCT RECIPE_ID FROM RECIPE_ITEM_INPUTS_ITEMS WHERE ITEM_INPUTS_ITEMS_ID=? ORDER BY RECIPE_ID LIMIT ?",
        (item_id, limit),
    )]
    return [load_recipe(conn, i) for i in ids]


def format_recipe(r: Recipe) -> str:
    left = " + ".join(r.inputs + r.fluid_inputs) or "(nothing)"
    right = " + ".join(r.outputs + r.fluid_outputs) or "(nothing)"
    line = f"{left} -> {right}"
    if r.eu_t is not None:
        amps = f" ({r.amps}A)" if r.amps is not None else ""
        line += f" | {r.eu_t} EU/t {r.tier}{amps}"
    if r.duration_ticks is not None:
        line += f" | {r.duration_ticks / 20:.1f} s"
    return line
