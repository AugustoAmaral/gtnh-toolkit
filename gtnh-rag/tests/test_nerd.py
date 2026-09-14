import json
from pathlib import Path

from gtnh_rag.nerd import load_stacks, nerd_chunks

FIX = Path(__file__).parent / "fixtures"


def test_load_stacks():
    stacks = load_stacks(FIX / "nerd_stacks_mini.json")
    assert stacks["77d3"] == "Industrial Bee"


def test_only_allowlisted_handlers_become_chunks():
    stacks = load_stacks(FIX / "nerd_stacks_mini.json")
    chunks = list(nerd_chunks(FIX / "nerd_mini.json", stacks, {"net.bdew.neiaddons.forestry.bees.BeeBreedingHandler"}))
    assert len(chunks) == 1
    c = chunks[0]
    assert c.source == "nei"
    assert c.doc_title == "Bee Breeding"
    assert c.section == "Industrial Bee"
    # Exact line (without duplicate outputs)
    assert "Imperial Bee + Noble Bee -> Industrial Bee" in c.text
    # Verify no duplicate outputs
    assert "Industrial Bee + Industrial Bee" not in c.text
    assert c.text.startswith("Bee Breeding > Industrial Bee\n")


def test_unknown_slugs_fall_back_to_slug():
    chunks = list(nerd_chunks(FIX / "nerd_mini.json", {}, {"codechicken.nei.recipe.FurnaceRecipeHandler"}))
    assert "4d0 -> 1d0" in chunks[0].text


def test_dict_query_item_uses_item_slug(tmp_path):
    """queryItem can be a stack dict (itemSlug/count/NBT), not just a slug string."""
    temp_json = tmp_path / "nerd_dict_query_item.json"
    data = {
        "version": "test",
        "queries": [
            {
                "queryItem": {"itemSlug": "9d0", "count": 1, "NBT": {"tag": {"SkullOwner": {"Name": "X"}}}},
                "handlers": [
                    {
                        "id": "test.handler",
                        "name": "Test Handler",
                        "tabName": "Test",
                        "recipes": [
                            {
                                "generic": {
                                    "ingredients": ["4d0"],
                                    "otherStacks": [],
                                    "outItem": "9d0",
                                }
                            }
                        ],
                    }
                ],
            }
        ],
    }
    temp_json.write_text(json.dumps(data))
    stacks = {"4d0": "Cobblestone", "9d0": "Player Head"}
    chunks = list(nerd_chunks(temp_json, stacks, {"test.handler"}))
    assert len(chunks) == 1
    assert chunks[0].section == "Player Head"


def test_null_stacks_are_skipped(tmp_path):
    """Verify that None entries in ingredients and otherStacks are skipped."""
    temp_json = tmp_path / "nerd_with_nulls.json"
    data = {
        "version": "test",
        "queries": [
            {
                "queryItem": "1d0",
                "handlers": [
                    {
                        "id": "test.handler",
                        "name": "Test Handler",
                        "tabName": "Test",
                        "recipes": [
                            {
                                "generic": {
                                    "ingredients": [None, "4d0", {"count": 1}],
                                    "otherStacks": [None],
                                    "outItem": "1d0",
                                }
                            }
                        ],
                    }
                ],
            }
        ],
    }
    temp_json.write_text(json.dumps(data))
    stacks = {"4d0": "Cobblestone", "1d0": "Stone"}
    chunks = list(nerd_chunks(temp_json, stacks, {"test.handler"}))
    assert len(chunks) == 1
    assert "Cobblestone -> Stone" in chunks[0].text
    assert "None" not in chunks[0].text


def test_cap_and_dedupe(tmp_path):
    """Verify 40-line cap and deduplication of recipe lines."""
    temp_json = tmp_path / "nerd_with_many_recipes.json"
    recipes = []
    # Recipes 0..39: distinct
    for i in range(40):
        recipes.append({
            "generic": {
                "ingredients": [f"s{i}"],
                "otherStacks": [],
                "outItem": "o",
            }
        })
    # Recipe 40: duplicate of recipe 0
    recipes.append({
        "generic": {
            "ingredients": ["s0"],
            "otherStacks": [],
            "outItem": "o",
        }
    })
    # Recipes 41..44: distinct
    for i in range(41, 45):
        recipes.append({
            "generic": {
                "ingredients": [f"s{i}"],
                "otherStacks": [],
                "outItem": "o",
            }
        })

    data = {
        "version": "test",
        "queries": [
            {
                "queryItem": "o",
                "handlers": [
                    {
                        "id": "test.handler",
                        "name": "Test",
                        "tabName": "Test",
                        "recipes": recipes,
                    }
                ],
            }
        ],
    }
    temp_json.write_text(json.dumps(data))
    stacks = {f"s{i}": f"Item{i}" for i in range(50)}
    stacks["o"] = "Output"
    chunks = list(nerd_chunks(temp_json, stacks, {"test.handler"}))
    assert len(chunks) == 1
    c = chunks[0]
    text_lines = c.text.split("\n")
    # First line is breadcrumb, rest are recipe lines
    recipe_lines = text_lines[1:]
    # Should have exactly 40 lines (cap)
    assert len(recipe_lines) == 40
    # Verify no duplicates (all should be unique)
    assert len(set(recipe_lines)) == len(recipe_lines)
