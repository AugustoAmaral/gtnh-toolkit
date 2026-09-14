import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from gtnh_rag.cli import main
from gtnh_rag.config import load_config

FIX = Path(__file__).parent / "fixtures"


def _config(tmp_path):
    cfg = tmp_path / "gtnh_rag.toml"
    cfg.write_text(f'data_dir = "{tmp_path / "data"}"\nembed_model = "fake"\nnerd_handlers = ["net.bdew.neiaddons.forestry.bees.BeeBreedingHandler"]\n')
    raw = tmp_path / "data" / "raw"
    (raw / "wiki").mkdir(parents=True)
    (raw / "vault").mkdir()
    (raw / "nesql").mkdir()
    (raw / "nerd").mkdir()
    (raw / "wiki" / "LFTR.wikitext").write_text("== Usage ==\nThe Liquid Fluoride Thorium Reactor burns thorium salt.")
    (raw / "nesql" / "nesql-db.script").write_text((FIX / "mini.script").read_text())
    (raw / "nerd" / "recipes.json").write_text((FIX / "nerd_mini.json").read_text())
    (raw / "nerd" / "recipes_stacks.json").write_text((FIX / "nerd_stacks_mini.json").read_text())
    return cfg


def test_help_runs():
    r = subprocess.run([sys.executable, "-m", "gtnh_rag.cli", "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "search" in r.stdout and "recipe" in r.stdout


def test_build_search_and_recipe(tmp_path, monkeypatch, capsys, fake_embedder):
    cfg = _config(tmp_path)
    monkeypatch.setattr("gtnh_rag.cli.make_embedder", lambda c, m=None, u=None: fake_embedder)
    assert main(["--config", str(cfg), "build"]) == 0
    out = capsys.readouterr().out
    assert "chunks" in out and "embedded" in out

    assert main(["--config", str(cfg), "search", "thorium reactor", "--json", "-k", "3"]) == 0
    hits = json.loads(capsys.readouterr().out)
    assert hits and hits[0]["doc_title"] in {"LFTR", "Liquid Fluoride Thorium Reactor", "Recipe: Liquid Fluoride Thorium Reactor (EV)", "Thorium"}
    assert {"id", "source", "doc_title", "section", "text", "score"} <= set(hits[0])

    assert main(["--config", str(cfg), "recipe", "Lithium Dust"]) == 0
    out = capsys.readouterr().out
    assert "Liquid Fluoride Thorium Reactor (EV)" in out and "2048 EU/t EV" in out

    assert main(["--config", str(cfg), "recipe", "Lithium Dust", "--uses"]) == 0
    assert "Crafting Table" in capsys.readouterr().out


def test_build_is_incremental(tmp_path, monkeypatch, capsys, fake_embedder):
    cfg = _config(tmp_path)
    monkeypatch.setattr("gtnh_rag.cli.make_embedder", lambda c, m=None, u=None: fake_embedder)
    main(["--config", str(cfg), "build"])
    capsys.readouterr()
    main(["--config", str(cfg), "build"])
    out = capsys.readouterr().out
    assert "embedded 0" in out


def test_build_skip_flags_keep_existing_chunks(tmp_path, monkeypatch, capsys, fake_embedder):
    cfg = _config(tmp_path)
    monkeypatch.setattr("gtnh_rag.cli.make_embedder", lambda c, m=None, u=None: fake_embedder)
    assert main(["--config", str(cfg), "build"]) == 0
    conn = sqlite3.connect(tmp_path / "data" / "index.sqlite")
    before = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    conn.close()
    capsys.readouterr()

    assert main(["--config", str(cfg), "build", "--skip-nesql", "--skip-nerd"]) == 0
    capsys.readouterr()

    conn = sqlite3.connect(tmp_path / "data" / "index.sqlite")
    after = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    quest_rows = conn.execute("SELECT COUNT(*) FROM chunks WHERE source='quest'").fetchone()[0]
    conn.close()
    assert after == before
    assert quest_rows > 0


def test_build_reimports_only_when_script_changes(tmp_path, monkeypatch, capsys, fake_embedder):
    cfg = _config(tmp_path)
    monkeypatch.setattr("gtnh_rag.cli.make_embedder", lambda c, m=None, u=None: fake_embedder)
    assert main(["--config", str(cfg), "build", "--skip-embed"]) == 0
    capsys.readouterr()

    recipes_db = tmp_path / "data" / "recipes.sqlite"
    mtime_before = recipes_db.stat().st_mtime

    assert main(["--config", str(cfg), "build", "--skip-embed"]) == 0
    out = capsys.readouterr().out
    assert recipes_db.stat().st_mtime == mtime_before
    assert "importing nesql" not in out

    script = tmp_path / "data" / "raw" / "nesql" / "nesql-db.script"
    with script.open("a") as fh:
        fh.write("\nINSERT INTO ITEM VALUES('i~x~y~0','item/x.png','y',0,9999,'Extra Item',0,64,'x','','tile.y')\n")

    assert main(["--config", str(cfg), "build", "--skip-embed"]) == 0
    out = capsys.readouterr().out
    assert "importing nesql" in out


def test_recipe_exit_code_when_no_recipes(tmp_path, monkeypatch, capsys, fake_embedder):
    cfg = _config(tmp_path)
    monkeypatch.setattr("gtnh_rag.cli.make_embedder", lambda c, m=None, u=None: fake_embedder)
    main(["--config", str(cfg), "build"])
    capsys.readouterr()

    assert main(["--config", str(cfg), "recipe", "Stone", "--uses"]) == 1
    out = capsys.readouterr().out
    assert "no recipes for" in out


def test_runtime_error_exits_2(tmp_path, monkeypatch, capsys, fake_embedder):
    cfg = _config(tmp_path)
    monkeypatch.setattr("gtnh_rag.cli.make_embedder", lambda c, m=None, u=None: fake_embedder)
    assert main(["--config", str(cfg), "build"]) == 0
    capsys.readouterr()

    class BadEmbedder:
        model = "fake"

        def embed(self, texts):
            raise RuntimeError("embedding failed at http://x with model y")

    monkeypatch.setattr("gtnh_rag.cli.make_embedder", lambda c, m=None, u=None: BadEmbedder())

    assert main(["--config", str(cfg), "search", "x"]) == 2
    err = capsys.readouterr().err
    assert "error: embedding failed" in err


def test_search_without_index_exits_2(tmp_path, capsys):
    cfg = tmp_path / "gtnh_rag.toml"
    cfg.write_text(f'data_dir = "{tmp_path / "data"}"\n')

    assert main(["--config", str(cfg), "search", "x"]) == 2
    err = capsys.readouterr().err
    assert "run `gtnh-rag build`" in err

    loaded_cfg = load_config(cfg)
    assert not loaded_cfg.index_path.exists()


def test_build_refuses_mass_prune_without_force(tmp_path, monkeypatch, capsys, fake_embedder):
    cfg = _config(tmp_path)
    monkeypatch.setattr("gtnh_rag.cli.make_embedder", lambda c, m=None, u=None: fake_embedder)
    assert main(["--config", str(cfg), "build", "--skip-embed"]) == 0
    capsys.readouterr()

    for f in (tmp_path / "data" / "raw" / "wiki").glob("*.wikitext"):
        f.unlink()

    assert main(["--config", str(cfg), "build", "--skip-embed"]) == 1
    err = capsys.readouterr().err
    assert "refusing to prune" in err
    conn = sqlite3.connect(tmp_path / "data" / "index.sqlite")
    wiki_rows = conn.execute("SELECT COUNT(*) FROM chunks WHERE source='wiki'").fetchone()[0]
    conn.close()
    assert wiki_rows > 0

    assert main(["--config", str(cfg), "build", "--skip-embed", "--force-prune"]) == 0
    conn = sqlite3.connect(tmp_path / "data" / "index.sqlite")
    wiki_rows = conn.execute("SELECT COUNT(*) FROM chunks WHERE source='wiki'").fetchone()[0]
    conn.close()
    assert wiki_rows == 0


def test_fetch_vault_optional(tmp_path, monkeypatch, capsys):
    cfg = tmp_path / "gtnh_rag.toml"
    cfg.write_text(f'data_dir = "{tmp_path / "data"}"\n')
    monkeypatch.setattr("gtnh_rag.cli.fetch_wiki", lambda api, out_dir: (0, []))

    assert main(["--config", str(cfg), "fetch", "--source", "vault"]) == 1
    err = capsys.readouterr().err
    assert "no vault_dir configured" in err

    assert main(["--config", str(cfg), "fetch", "--source", "all"]) == 0
    out = capsys.readouterr().out
    assert "vault: no vault_dir configured, skipping" in out


def test_fetch_vault_copies_when_configured(tmp_path, monkeypatch, capsys):
    notes = tmp_path / "notes" / "wiki"
    notes.mkdir(parents=True)
    (notes / "x.md").write_text("---\ntags: [gtnh]\n---\n# X\nbody")
    cfg = tmp_path / "gtnh_rag.toml"
    cfg.write_text(f'data_dir = "{tmp_path / "data"}"\nvault_dir = "{tmp_path / "notes"}"\n')
    monkeypatch.setattr("gtnh_rag.cli.fetch_wiki", lambda api, out_dir: (0, []))

    assert main(["--config", str(cfg), "fetch", "--source", "vault"]) == 0
    out = capsys.readouterr().out
    assert "vault: 1 pages copied" in out
    assert (tmp_path / "data" / "raw" / "vault" / "wiki__x.md").exists()
