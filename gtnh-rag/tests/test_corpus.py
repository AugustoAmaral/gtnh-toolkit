import sqlite3
from pathlib import Path

import pytest

from gtnh_rag.corpus import quest_chunks, recipe_chunks, vault_chunks, wiki_chunks
from gtnh_rag.nesql import import_script

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "r.sqlite"
    import_script(FIX / "mini.script", db)
    c = sqlite3.connect(db)
    yield c
    c.close()


def test_wiki_chunks_use_title_from_filename(tmp_path):
    (tmp_path / "Bees__Guide.wikitext").write_text("Intro.\n== Usage ==\nBees breed.")
    chunks = list(wiki_chunks(tmp_path))
    assert {c.doc_title for c in chunks} == {"Bees/Guide"}
    assert chunks[-1].section == "Usage"
    assert chunks[-1].source == "wiki"


def test_wiki_chunks_skip_redirects(tmp_path):
    (tmp_path / "Old__Name.wikitext").write_text("#REDIRECT [[New Name]]")
    (tmp_path / "Bees.wikitext").write_text("Intro.\n== Usage ==\nBees breed.")
    chunks = list(wiki_chunks(tmp_path))
    assert {c.doc_title for c in chunks} == {"Bees"}


def test_vault_chunks_strip_frontmatter_and_take_h1(tmp_path):
    (tmp_path / "wiki__fontes__x.md").write_text("---\ntype: fonte\ntags: [gtnh]\n---\n# CropsNH\nBody.\n## Cura\nPlant Cure.")
    chunks = list(vault_chunks(tmp_path))
    assert chunks[0].doc_title == "CropsNH"
    assert "type: fonte" not in chunks[0].text
    assert chunks[-1].section == "Cura" and chunks[-1].source == "vault"


def test_quest_chunks(conn):
    chunks = {c.doc_title: c for c in quest_chunks(conn)}
    q = chunks["Liquid Fluoride Thorium Reactor"]
    assert q.source == "quest" and q.section == "Nuclear Power"
    assert "Requires: Thorium" in q.text
    assert "Build the LFTR." in q.text
    assert "Tasks: Submit thorium (2x Thorium Dust)" in q.text
    assert "Rewards: 1x Lithium Dust" in q.text
    assert chunks["Thorium"].text.count("Requires:") == 0


def test_recipe_chunks_group_by_type_and_output(conn):
    chunks = list(recipe_chunks(conn))
    titles = {(c.doc_title, c.section) for c in chunks}
    assert ("Recipe: Liquid Fluoride Thorium Reactor (EV)", "Lithium Dust") in titles
    assert ("Recipe: Crafting Table", "Stone") in titles
    lftr = next(c for c in chunks if c.section == "Lithium Dust")
    assert "2x Thorium Dust + 100 mB Lithium Fluoride -> 1x Lithium Dust @50% + 200 mB Helium | 2048 EU/t EV (1A) | 180.0 s" in lftr.text
    assert lftr.source == "recipe"


def test_recipe_chunks_include_fluid_only_recipes(conn):
    # Insert a fluid-only recipe (no item outputs)
    conn.execute("INSERT INTO RECIPE VALUES(?, ?)", ("r~gas1", "rt~gregtech~gtpp.recipe.lftr~EV"))
    conn.execute("INSERT INTO RECIPE_FLUID_OUTPUTS VALUES(?, ?, ?, ?, ?)", ("r~gas1", 500, "f~gregtech~helium", 1.0, 0))
    conn.commit()
    chunks = list(recipe_chunks(conn))
    titles = {(c.doc_title, c.section) for c in chunks}
    assert ("Recipe: Liquid Fluoride Thorium Reactor (EV)", "Helium") in titles
    helium = next(c for c in chunks if c.section == "Helium")
    assert "-> 500 mB Helium" in helium.text


def test_quest_in_two_lines_gets_joined_section(conn):
    # Add an alternate quest line
    conn.execute("INSERT INTO QUEST_LINE VALUES(?, ?, ?, ?, ?, ?)", ("ql~alt", "", "Alt Line", "ql-2", "NORMAL", None))
    conn.execute("INSERT INTO QUEST_LINE_QUEST VALUES(?, ?)", ("ql~alt", "q~lftr"))
    conn.commit()
    chunks = {c.doc_title: c for c in quest_chunks(conn)}
    q = chunks["Liquid Fluoride Thorium Reactor"]
    assert q.section == "Alt Line / Nuclear Power"
    assert q.text.startswith("Liquid Fluoride Thorium Reactor > Alt Line / Nuclear Power\n")


def test_vault_h1_removed_by_span_not_first_occurrence(tmp_path):
    (tmp_path / "wiki__fontes__test.md").write_text("Mentions # CropsNH in prose.\n\n# CropsNH\nBody.\n")
    chunks = list(vault_chunks(tmp_path))
    assert chunks[0].doc_title == "CropsNH"
    assert "Mentions # CropsNH in prose." in chunks[0].text
    assert not any(c.section == "CropsNH" for c in chunks)
