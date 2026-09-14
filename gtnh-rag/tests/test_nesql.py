import sqlite3
from pathlib import Path

from gtnh_rag.nesql import convert_statement, import_script, unescape

FIX = Path(__file__).parent / "fixtures" / "mini.script"


def test_unescape_unicode_sequences():
    assert unescape(r"a\u000ab") == "a\nb"
    assert unescape(r"caf\u00e9") == "café"
    assert unescape("plain") == "plain"


def test_convert_statement_rewrites_create_and_skips_noise():
    assert convert_statement("CREATE MEMORY TABLE PUBLIC.X(ID VARCHAR(255) NOT NULL PRIMARY KEY,CONSTRAINT FK1 FOREIGN KEY(ID) REFERENCES PUBLIC.Y(ID))") == \
        "CREATE TABLE X(ID VARCHAR(255) NOT NULL PRIMARY KEY,CONSTRAINT FK1 FOREIGN KEY(ID) REFERENCES Y(ID))"
    assert convert_statement("CREATE INDEX I1 ON PUBLIC.X(ID)") == "CREATE INDEX I1 ON X(ID)"
    assert convert_statement("INSERT INTO X VALUES('a',1,TRUE)") == "INSERT INTO X VALUES('a',1,TRUE)"
    assert convert_statement("SET DATABASE UNIQUE NAME HSQLDBA0749E61B0") is None
    assert convert_statement("ALTER TABLE PUBLIC.X ADD CONSTRAINT FK FOREIGN KEY(A) REFERENCES PUBLIC.Y(ID)") is None
    assert convert_statement("GRANT USAGE ON DOMAIN INFORMATION_SCHEMA.SQL_IDENTIFIER TO PUBLIC") is None


def test_import_script_builds_queryable_db(tmp_path):
    db = tmp_path / "recipes.sqlite"
    rows = import_script(FIX, db)
    assert rows > 30
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM ITEM").fetchone()[0] == 3
    name = conn.execute("SELECT LOCALIZED_NAME FROM ITEM WHERE ID='i~gregtech~gt.metaitem.01~32700'").fetchone()[0]
    assert name == "Thorium Dust"
    info = conn.execute("SELECT ADDITIONAL_INFO FROM GREG_TECH_RECIPE WHERE RECIPE_ID='r~lftr1'").fetchone()[0]
    assert info == "Fuel for the LFTR\nSecond line"
    desc = conn.execute("SELECT DESCRIPTION FROM QUEST WHERE ID='q~lftr'").fetchone()[0]
    assert desc == "Build the LFTR.\nIt's worth it."
    idx = {r[1] for r in conn.execute("PRAGMA index_list('ITEM')")}
    assert "idx_item_name" in idx
    igs_idx = {r[1] for r in conn.execute("PRAGMA index_list('ITEM_GROUP_ITEM_STACKS')")}
    assert "idx_igs_group" in igs_idx
    fgs_idx = {r[1] for r in conn.execute("PRAGMA index_list('FLUID_GROUP_FLUID_STACKS')")}
    assert "idx_fgs_group" in fgs_idx
    fout_idx = {r[1] for r in conn.execute("PRAGMA index_list('RECIPE_FLUID_OUTPUTS')")}
    assert "idx_fout_recipe" not in fout_idx


def test_import_overwrites_existing_db(tmp_path):
    db = tmp_path / "recipes.sqlite"
    import_script(FIX, db)
    import_script(FIX, db)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM ITEM").fetchone()[0] == 3


def test_unescape_surrogate_pair():
    assert unescape("\\ud83d\\ude00") == "😀"


def test_import_script_skips_uninitialized_tables(tmp_path):
    db = tmp_path / "recipes.sqlite"
    import_script(FIX, db)
    conn = sqlite3.connect(db)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "BLOCKS" not in tables
