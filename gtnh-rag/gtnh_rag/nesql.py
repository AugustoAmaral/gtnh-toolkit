"""Import a nesql-exporter HSQLDB .script (SQL text) into SQLite."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

_UNICODE = re.compile(r"\\u([0-9a-fA-F]{4})")
_SURROGATE_PAIR = re.compile(r"\\u([dD][89abAB][0-9a-fA-F]{2})\\u([dD][c-fC-F][0-9a-fA-F]{2})")
_SKIP_PREFIXES = ("SET ", "GRANT ", "ALTER ", "CREATE USER", "CREATE SCHEMA")
_CREATE_TABLE_NAME = re.compile(r"^CREATE TABLE (\w+)\(")
_INSERT_TABLE_NAME = re.compile(r"^INSERT INTO (\w+) VALUES\(")

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_item_name ON ITEM(LOCALIZED_NAME)",
    "CREATE INDEX IF NOT EXISTS idx_out_item ON RECIPE_ITEM_OUTPUTS(ITEM_OUTPUTS_VALUE_ITEM_ID)",
    "CREATE INDEX IF NOT EXISTS idx_recipe_type ON RECIPE(RECIPE_TYPE_ID)",
    "CREATE INDEX IF NOT EXISTS idx_in_item ON RECIPE_ITEM_INPUTS_ITEMS(ITEM_INPUTS_ITEMS_ID)",
    "CREATE INDEX IF NOT EXISTS idx_gtr_recipe ON GREG_TECH_RECIPE(RECIPE_ID)",
    "CREATE INDEX IF NOT EXISTS idx_fluid_name ON FLUID(LOCALIZED_NAME)",
    "CREATE INDEX IF NOT EXISTS idx_igs_group ON ITEM_GROUP_ITEM_STACKS(ITEM_GROUP_ID)",
    "CREATE INDEX IF NOT EXISTS idx_fgs_group ON FLUID_GROUP_FLUID_STACKS(FLUID_GROUP_ID)",
)


def _combine_surrogate_pair(m: re.Match[str]) -> str:
    high = int(m.group(1), 16)
    low = int(m.group(2), 16)
    return chr(0x10000 + (high - 0xD800) * 0x400 + (low - 0xDC00))


def unescape(s: str) -> str:
    # HSQLDB encodes characters outside the BMP (e.g. some emoji in item names)
    # as a UTF-16 surrogate pair of two \uXXXX escapes; combine those before
    # decoding remaining single-codepoint escapes, since a lone surrogate
    # codepoint cannot be encoded to UTF-8.
    s = _SURROGATE_PAIR.sub(_combine_surrogate_pair, s)
    return _UNICODE.sub(lambda m: chr(int(m.group(1), 16)), s)


def convert_statement(line: str) -> str | None:
    """Return a SQLite statement for one HSQLDB script line, or None to skip it."""
    line = line.rstrip("\n")
    if not line or line.startswith(_SKIP_PREFIXES):
        return None
    if line.startswith("INSERT INTO "):
        return unescape(line)
    if line.startswith("CREATE MEMORY TABLE ") or line.startswith("CREATE CACHED TABLE "):
        line = line.replace("CREATE MEMORY TABLE ", "CREATE TABLE ", 1).replace("CREATE CACHED TABLE ", "CREATE TABLE ", 1)
        return line.replace("PUBLIC.", "")
    if line.startswith("CREATE INDEX ") or line.startswith("CREATE UNIQUE INDEX "):
        return line.replace("PUBLIC.", "")
    return None


def import_script(script_path: Path, db_path: Path, batch: int = 5000) -> int:
    """Rebuild db_path from the script. Returns the number of INSERT statements executed."""
    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA foreign_keys=OFF")
    inserted = 0
    pending: list[str] = []
    known_tables: set[str] = set()
    try:
        with Path(script_path).open("r", encoding="utf-8") as fh:
            for raw in fh:
                stmt = convert_statement(raw)
                if stmt is None:
                    continue
                if stmt.startswith("INSERT"):
                    match = _INSERT_TABLE_NAME.match(stmt)
                    if match and match.group(1) not in known_tables:
                        # Some HSQLDB internal system tables (e.g. BLOCKS, used for
                        # identity/sequence block allocation) receive INSERTs but are
                        # never CREATEd in the exported script; skip them.
                        continue
                    pending.append(stmt)
                    if len(pending) >= batch:
                        conn.executescript(";\n".join(pending) + ";")
                        inserted += len(pending)
                        pending.clear()
                else:
                    if pending:
                        conn.executescript(";\n".join(pending) + ";")
                        inserted += len(pending)
                        pending.clear()
                    conn.execute(stmt)
                    match = _CREATE_TABLE_NAME.match(stmt)
                    if match:
                        known_tables.add(match.group(1))
        if pending:
            conn.executescript(";\n".join(pending) + ";")
            inserted += len(pending)
        for idx in INDEXES:
            conn.execute(idx)
        conn.commit()
    finally:
        conn.close()
    return inserted
