"""Turn raw sources into Chunks."""
from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from .chunk import Chunk, split_sections
from .convert import wikitext_to_markdown
from .recipes import format_recipe, item_group_label, load_recipe

_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)


def wiki_chunks(wiki_raw_dir: Path) -> Iterator[Chunk]:
    for path in sorted(Path(wiki_raw_dir).glob("*.wikitext")):
        raw = path.read_text(encoding="utf-8")
        if raw.strip().lower().startswith("#redirect"):
            continue
        title = path.stem.replace("__", "/")
        md = wikitext_to_markdown(raw)
        yield from split_sections(md, title, "wiki")


def vault_chunks(vault_raw_dir: Path) -> Iterator[Chunk]:
    for path in sorted(Path(vault_raw_dir).glob("*.md")):
        text = _FRONTMATTER.sub("", path.read_text(encoding="utf-8"), count=1)
        m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        title = m.group(1).strip() if m else path.stem
        if m:
            text = text[:m.start()] + text[m.end():]
        yield from split_sections(text, title, "vault")


def quest_chunks(conn: sqlite3.Connection) -> Iterator[Chunk]:
    lines_map: dict[str, list[str]] = {}
    for quest_id, line_name in conn.execute("SELECT ql.QUESTS_ID, l.NAME FROM QUEST_LINE_QUEST ql JOIN QUEST_LINE l ON l.ID=ql.QUEST_LINES_ID ORDER BY l.NAME"):
        if quest_id not in lines_map:
            lines_map[quest_id] = []
        lines_map[quest_id].append(line_name)
    for qid, name, desc in conn.execute("SELECT ID, NAME, DESCRIPTION FROM QUEST ORDER BY ID"):
        parts = []
        reqs = [r[0] for r in conn.execute(
            "SELECT q.NAME FROM QUEST_QUEST qq JOIN QUEST q ON q.ID=qq.REQUIRED_QUESTS_ID WHERE qq.REQUIRED_BY_QUESTS_ID=? ORDER BY q.NAME",
            (qid,),
        )]
        if reqs:
            parts.append("Requires: " + ", ".join(reqs))
        parts.append(desc.strip())
        tasks = []
        for tid, tname in conn.execute(
            "SELECT t.ID, t.NAME FROM QUEST_TASK qt JOIN TASK t ON t.ID=qt.TASKS_ID WHERE qt.QUEST_ID=? ORDER BY qt.TASKS_ORDER",
            (qid,),
        ):
            items = [item_group_label(conn, g) for (g,) in conn.execute(
                "SELECT ITEMS_ID FROM TASK_ITEM_GROUP WHERE TASK_ID=? ORDER BY ITEMS_ORDER", (tid,))]
            tasks.append(f"{tname} ({', '.join(items)})" if items else tname)
        if tasks:
            parts.append("Tasks: " + "; ".join(tasks))
        rewards = []
        for (rid,) in conn.execute(
            "SELECT REWARDS_ID FROM QUEST_REWARD WHERE QUEST_ID=? ORDER BY REWARDS_ORDER", (qid,)):
            rewards.extend(item_group_label(conn, g) for (g,) in conn.execute(
                "SELECT ITEMS_ID FROM REWARD_ITEM_GROUP WHERE REWARD_ID=? ORDER BY ITEMS_ORDER", (rid,)))
        if rewards:
            parts.append("Rewards: " + ", ".join(rewards))
        section = " / ".join(lines_map.get(qid, []))
        crumb = f"{name} > {section}" if section else name
        yield Chunk("quest", name, section, crumb + "\n" + "\n".join(parts))


def recipe_chunks(conn: sqlite3.Connection, max_per_chunk: int = 20) -> Iterator[Chunk]:
    rows = conn.execute(
        "SELECT t.TYPE, COALESCE(i.LOCALIZED_NAME, f.LOCALIZED_NAME) AS out_name, r.ID "
        "FROM RECIPE r "
        "JOIN RECIPE_TYPE t ON t.ID = r.RECIPE_TYPE_ID "
        "LEFT JOIN RECIPE_ITEM_OUTPUTS o ON o.RECIPE_ID = r.ID AND o.ITEM_OUTPUTS_KEY = 0 "
        "LEFT JOIN ITEM i ON i.ID = o.ITEM_OUTPUTS_VALUE_ITEM_ID "
        "LEFT JOIN RECIPE_FLUID_OUTPUTS fo ON fo.RECIPE_ID = r.ID AND fo.FLUID_OUTPUTS_KEY = 0 "
        "LEFT JOIN FLUID f ON f.ID = fo.FLUID_OUTPUTS_VALUE_FLUID_ID "
        "WHERE i.ID IS NOT NULL OR f.ID IS NOT NULL "
        "ORDER BY t.TYPE, out_name, r.ID"
    )
    current_key = None
    lines: list[str] = []

    def flush():
        if current_key and lines:
            type_name, item = current_key
            title = f"Recipe: {type_name}"
            for i in range(0, len(lines), max_per_chunk):
                body = "\n".join(lines[i:i + max_per_chunk])
                yield Chunk("recipe", title, item, f"{title} > {item}\n{body}")

    for type_name, out_name, recipe_id in rows:
        key = (type_name, out_name)
        if key != current_key:
            yield from flush()
            current_key, lines = key, []
        lines.append(format_recipe(load_recipe(conn, recipe_id)))
    yield from flush()
