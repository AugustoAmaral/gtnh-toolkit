import sqlite3
from pathlib import Path

import pytest

from gtnh_rag.nesql import import_script
from gtnh_rag.recipes import _fluid_group_label, find_items, format_recipe, item_group_label, load_recipe, recipes_producing, recipes_using

FIX = Path(__file__).parent / "fixtures" / "mini.script"


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "r.sqlite"
    import_script(FIX, db)
    c = sqlite3.connect(db)
    yield c
    c.close()


def test_find_items_exact_before_partial(conn):
    hits = find_items(conn, "thorium dust")
    assert hits[0] == ("i~gregtech~gt.metaitem.01~32700", "Thorium Dust")
    assert find_items(conn, "dust")[0][1] in {"Thorium Dust", "Lithium Dust"}
    assert find_items(conn, "zzz") == []


def test_load_recipe_resolves_names_and_gt_fields(conn):
    r = load_recipe(conn, "r~lftr1")
    assert r.type_name == "Liquid Fluoride Thorium Reactor (EV)"
    assert r.inputs == ["2x Thorium Dust"]
    assert r.fluid_inputs == ["100 mB Lithium Fluoride"]
    assert r.outputs == ["1x Lithium Dust @50%"]
    assert r.fluid_outputs == ["200 mB Helium"]
    assert (r.eu_t, r.tier, r.amps, r.duration_ticks) == (2048, "EV", 1, 3600)
    assert r.info.startswith("Fuel for the LFTR")


def test_non_gt_recipe_has_no_power_fields(conn):
    r = load_recipe(conn, "r~stone1")
    assert r.type_name == "Crafting Table"
    assert r.eu_t is None and r.duration_ticks is None
    assert r.outputs == ["4x Stone"]


def test_producing_and_using(conn):
    assert [r.recipe_id for r in recipes_producing(conn, "i~gregtech~gt.metaitem.01~32701")] == ["r~lftr1"]
    assert [r.recipe_id for r in recipes_using(conn, "i~gregtech~gt.metaitem.01~32701")] == ["r~stone1"]


def test_format_recipe(conn):
    line = format_recipe(load_recipe(conn, "r~lftr1"))
    assert line == "2x Thorium Dust + 100 mB Lithium Fluoride -> 1x Lithium Dust @50% + 200 mB Helium | 2048 EU/t EV (1A) | 180.0 s"
    assert format_recipe(load_recipe(conn, "r~stone1")) == "1x Lithium Dust -> 4x Stone"


def test_fluid_group_with_alternatives(conn):
    # Insert two extra FLUID rows
    conn.execute(
        "INSERT INTO FLUID VALUES('f~gregtech~molten_salt',1000,902,FALSE,'fluid/ms.png','molten_salt','Molten Salt',0,'gregtech','',1000,'fluid.molten_salt',1000)"
    )
    conn.execute(
        "INSERT INTO FLUID VALUES('f~gregtech~brine',1000,903,FALSE,'fluid/br.png','brine','Brine',0,'gregtech','',300,'fluid.brine',1000)"
    )
    conn.commit()

    # Create a new fluid group with three alternatives
    conn.execute("INSERT INTO FLUID_GROUP VALUES('fg~alt')")
    conn.execute("INSERT INTO FLUID_GROUP_FLUID_STACKS VALUES('fg~alt',50,'f~gregtech~brine')")
    conn.execute("INSERT INTO FLUID_GROUP_FLUID_STACKS VALUES('fg~alt',50,'f~gregtech~helium')")
    conn.execute("INSERT INTO FLUID_GROUP_FLUID_STACKS VALUES('fg~alt',50,'f~gregtech~molten_salt')")
    conn.commit()

    # Verify the new fluid group renders all three alternatives in sorted order
    assert _fluid_group_label(conn, 'fg~alt') == "50 mB Brine / Helium / Molten Salt"

    # Verify existing single-fluid group still works correctly
    assert load_recipe(conn, "r~lftr1").fluid_inputs == ["100 mB Lithium Fluoride"]


def test_item_group_label_plus_n(conn):
    # Insert 4 extra ITEM rows
    for i, name in enumerate(['A1', 'A2', 'A3', 'A4'], 1):
        conn.execute(
            "INSERT INTO ITEM VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"i~test~a{i}", "item/a.png", f"a{i}", 0, 100+i, name, 0, 1, "test", "", f"a{i}")
        )
    conn.commit()

    # Create an ITEM_GROUP with 4 stacks of size 1
    conn.execute("INSERT INTO ITEM_GROUP VALUES(?, ?)", ('ig~many', None))
    for i in range(1, 5):
        conn.execute("INSERT INTO ITEM_GROUP_ITEM_STACKS VALUES(?, ?, ?)", ("ig~many", f"i~test~a{i}", 1))
    conn.commit()

    # Verify the format: "1x A1 / A2 / A3 / +1"
    assert item_group_label(conn, 'ig~many') == "1x A1 / A2 / A3 / +1"
