"""Secondary recipe source: NotEnoughRecipeDumps JSON, streamed with ijson."""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import ijson

from .chunk import Chunk


def load_stacks(stacks_json: Path) -> dict[str, str]:
    with Path(stacks_json).open("rb") as fh:
        data = json.load(fh)
    return {slug: entry.get("displayName", slug) for slug, entry in data.get("items", {}).items()}


def _name(stack, stacks: dict[str, str]) -> str:
    if isinstance(stack, str):
        return stacks.get(stack, stack)
    slug = stack.get("itemSlug", "?")
    count = stack.get("count", 1)
    label = stacks.get(slug, slug)
    return f"{count}x {label}" if count and count != 1 else label


def _recipe_line(recipe: dict, stacks: dict[str, str]) -> str:
    g = recipe.get("generic", {})
    # Skip None entries and dict entries without itemSlug
    ingredients = [s for s in g.get("ingredients", []) if s is not None and (isinstance(s, str) or "itemSlug" in s)]
    left = " + ".join(_name(s, stacks) for s in ingredients)
    outs = []
    out_item = g.get("outItem")
    if out_item is not None:
        outs.append(_name(out_item, stacks))
    other_stacks = [s for s in g.get("otherStacks", []) if s is not None and (isinstance(s, str) or "itemSlug" in s)]
    outs.extend(_name(s, stacks) for s in other_stacks)
    # Dedupe outputs preserving order
    outs = list(dict.fromkeys(outs))
    return f"{left or '(nothing)'} -> {' + '.join(outs) or '(nothing)'}"


def nerd_chunks(recipes_json: Path, stacks: dict[str, str], handlers: set[str]) -> Iterator[Chunk]:
    with Path(recipes_json).open("rb") as fh:
        for query in ijson.items(fh, "queries.item"):
            query_item = query.get("queryItem")
            item_name = _name(query_item, stacks) if query_item is not None else "?"
            for handler in query.get("handlers", []):
                if handler.get("id") not in handlers:
                    continue
                title = handler.get("name") or handler.get("tabName") or handler["id"]
                lines = [_recipe_line(r, stacks) for r in handler.get("recipes", [])]
                lines = list(dict.fromkeys(lines))  # dedupe, keep order
                if not lines:
                    continue
                yield Chunk("nei", title, item_name, f"{title} > {item_name}\n" + "\n".join(lines[:40]))
