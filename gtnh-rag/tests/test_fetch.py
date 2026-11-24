import json
import os
from pathlib import Path

import pytest

from gtnh_rag.fetch import fetch_vault, fetch_wiki, list_all_pages, safe_name, update_manifest


class FakeResp:
    def __init__(self, payload):
        self._p = payload
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class FakeSession:
    """Serves allpages in two batches and page content by title."""

    headers = {}

    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        if params.get("list") == "allpages":
            if "apcontinue" not in params:
                return FakeResp({"continue": {"apcontinue": "M"}, "query": {"allpages": [{"title": "LFTR"}, {"title": "Bees/Guide"}]}})
            return FakeResp({"query": {"allpages": [{"title": "Zinc"}]}})
        title = params["titles"]
        if title == "Zinc":
            return FakeResp({"query": {"pages": {"-1": {"missing": ""}}}})
        return FakeResp({"query": {"pages": {"1": {"title": title, "revisions": [{"slots": {"main": {"*": f"== {title} ==\ncontent"}}}]}}}})


def test_list_all_pages_follows_continue():
    assert list_all_pages("http://x/api.php", FakeSession()) == ["LFTR", "Bees/Guide", "Zinc"]


def test_safe_name():
    assert safe_name("Bees/Guide") == "Bees__Guide"


def test_fetch_wiki_writes_files_and_reports_failures(tmp_path, monkeypatch):
    monkeypatch.setattr("gtnh_rag.fetch.time.sleep", lambda s: None)
    written, failed, _ = fetch_wiki("http://x/api.php", tmp_path, session=FakeSession())
    assert written == 2
    assert failed == ["Zinc"]
    assert (tmp_path / "LFTR.wikitext").read_text() == "== LFTR ==\ncontent"
    assert (tmp_path / "Bees__Guide.wikitext").exists()


def test_fetch_vault_filters_by_path_or_tag(tmp_path):
    vault = tmp_path / "notes"
    (vault / "wiki" / "fontes").mkdir(parents=True)
    (vault / "wiki" / "entidades").mkdir(parents=True)
    (vault / "frentes" / "x").mkdir(parents=True)
    (vault / "wiki" / "fontes" / "cropsnh-gtnh-2-9.md").write_text("---\ntags: [crops]\n---\n# a")
    (vault / "wiki" / "entidades" / "server-setup.md").write_text("---\ntags: [infra, gtnh]\n---\n# b")
    (vault / "wiki" / "entidades" / "router.md").write_text("---\ntags: [infra]\n---\n# c")
    (vault / "frentes" / "x" / "x.md").write_text("# no frontmatter")
    out = tmp_path / "out"
    copied, _ = fetch_vault(vault, out)
    names = sorted(p.name for p in copied)
    assert names == ["cropsnh-gtnh-2-9.md", "server-setup.md"]
    assert (out / "wiki__entidades__server-setup.md").exists()


def test_update_manifest_merges(tmp_path):
    update_manifest(tmp_path, {"a": {"bytes": 1}})
    update_manifest(tmp_path, {"b": {"bytes": 2}})
    data = json.loads((tmp_path / "manifest.json").read_text())
    assert set(data) == {"a", "b"}


def test_fetch_wiki_sleeps_after_failures_too(tmp_path, monkeypatch):
    sleeps = []
    monkeypatch.setattr("gtnh_rag.fetch.time.sleep", lambda s: sleeps.append(s))
    written, failed, _ = fetch_wiki("http://x/api.php", tmp_path, session=FakeSession())
    assert written == 2
    assert failed == ["Zinc"]
    assert len(sleeps) == 4  # 1 from pagination + 3 from page loop (success, success, failure)
    assert all(s == 0.2 for s in sleeps)


def test_list_all_pages_sleeps_between_batches(monkeypatch):
    sleeps = []
    monkeypatch.setattr("gtnh_rag.fetch.time.sleep", lambda s: sleeps.append(s))
    list_all_pages("http://x/api.php", FakeSession())
    assert len(sleeps) == 1  # exactly 1 sleep between the two pagination batches
    assert sleeps[0] == 0.2


# --- stale raw files -------------------------------------------------------------------------

from gtnh_rag.fetch import prune_stale  # noqa: E402


class ListedSession(FakeSession):
    """Wiki whose page list is whatever `self.titles` holds; titles in `self.broken` fail to download."""

    def __init__(self, titles, broken=()):
        super().__init__()
        self.titles, self.broken = list(titles), set(broken)

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        if params.get("list") == "allpages":
            return FakeResp({"query": {"allpages": [{"title": t} for t in self.titles]}})
        title = params["titles"]
        if title in self.broken:
            raise ConnectionError("reset by peer")
        return FakeResp({"query": {"pages": {"1": {"title": title, "revisions": [{"slots": {"main": {"*": f"new {title}"}}}]}}}})


def _no_sleep(monkeypatch):
    monkeypatch.setattr("gtnh_rag.fetch.time.sleep", lambda s: None)


def test_fetch_wiki_removes_pages_gone_from_the_wiki(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    fetch_wiki("http://x/api.php", tmp_path, session=ListedSession(["A", "B", "C", "Old/Page"]))
    (tmp_path / "notes.txt").write_text("not a wiki page")
    written, failed, pruned = fetch_wiki("http://x/api.php", tmp_path, session=ListedSession(["A", "B", "C"]))
    assert (written, failed) == (3, [])
    assert pruned.removed == ["Old__Page.wikitext"] and pruned.refused == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["A.wikitext", "B.wikitext", "C.wikitext", "notes.txt"]


def test_fetch_wiki_keeps_old_copy_of_page_that_failed_to_download(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    fetch_wiki("http://x/api.php", tmp_path, session=ListedSession(["A", "B", "C"]))
    written, failed, pruned = fetch_wiki("http://x/api.php", tmp_path, session=ListedSession(["A", "B", "C"], broken={"B"}))
    assert failed == ["B"] and pruned.removed == []
    assert (tmp_path / "B.wikitext").read_text() == "new B"


def test_fetch_wiki_listing_error_removes_nothing(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    fetch_wiki("http://x/api.php", tmp_path, session=ListedSession(["A", "B"]))

    class Down(FakeSession):
        def get(self, url, params=None, timeout=None):
            raise ConnectionError("wiki down")

    with pytest.raises(ConnectionError):
        fetch_wiki("http://x/api.php", tmp_path, session=Down())
    assert sorted(p.name for p in tmp_path.iterdir()) == ["A.wikitext", "B.wikitext"]


def test_fetch_wiki_refuses_mass_removal_unless_forced(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    fetch_wiki("http://x/api.php", tmp_path, session=ListedSession(["A", "B", "C", "D"]))
    _, _, pruned = fetch_wiki("http://x/api.php", tmp_path, session=ListedSession(["A"]))
    assert pruned.removed == [] and pruned.refused == ["B.wikitext", "C.wikitext", "D.wikitext"] and pruned.total == 4
    assert len(list(tmp_path.iterdir())) == 4
    _, _, pruned = fetch_wiki("http://x/api.php", tmp_path, session=ListedSession(["A"]), force_prune=True)
    assert pruned.removed == ["B.wikitext", "C.wikitext", "D.wikitext"]
    assert [p.name for p in tmp_path.iterdir()] == ["A.wikitext"]


def _vault(tmp_path, notes):
    vault = tmp_path / "notes"
    for rel, tags in notes.items():
        (vault / rel).parent.mkdir(parents=True, exist_ok=True)
        (vault / rel).write_text(f"---\ntags: [{tags}]\n---\n# {rel}")
    return vault


def test_fetch_vault_removes_untagged_and_deleted_notes(tmp_path):
    vault = _vault(tmp_path, {"wiki/a.md": "gtnh", "wiki/b.md": "gtnh", "wiki/c.md": "gtnh", "frentes/d.md": "gtnh"})
    out = tmp_path / "out"
    fetch_vault(vault, out)
    (vault / "wiki" / "b.md").write_text("---\ntags: [infra]\n---\n# b")   # lost the tag
    (vault / "frentes" / "d.md").unlink()                                 # deleted
    copied, pruned = fetch_vault(vault, out)
    assert sorted(p.name for p in copied) == ["a.md", "c.md"]
    assert pruned.removed == ["frentes__d.md", "wiki__b.md"]
    assert sorted(p.name for p in out.iterdir()) == ["wiki__a.md", "wiki__c.md"]


def test_fetch_vault_missing_vault_dir_removes_nothing(tmp_path):
    vault = _vault(tmp_path, {"wiki/a.md": "gtnh", "wiki/b.md": "gtnh"})
    out = tmp_path / "out"
    fetch_vault(vault, out)
    copied, pruned = fetch_vault(tmp_path / "unmounted", out)
    assert copied == [] and pruned.removed == [] and len(pruned.refused) == 2
    assert len(list(out.iterdir())) == 2


def test_prune_stale_keeps_file_that_is_a_kept_name_under_another_spelling(tmp_path):
    # Case-insensitive filesystems: writing "Ore.wikitext" over "ORE.wikitext" keeps the old
    # entry name. Simulated with a hard link: two names, one file.
    (tmp_path / "ORE.wikitext").write_text("fresh")
    os.link(tmp_path / "ORE.wikitext", tmp_path / "Ore.wikitext")
    (tmp_path / "Gone.wikitext").write_text("stale")
    (tmp_path / "Other.wikitext").write_text("kept")
    pruned = prune_stale(tmp_path, {"Ore.wikitext", "Other.wikitext"}, "*.wikitext")
    assert pruned.removed == ["Gone.wikitext"]
    assert (tmp_path / "ORE.wikitext").read_text() == "fresh"


def test_prune_stale_threshold_matches_build(tmp_path):
    for name in ("a", "b", "c", "d"):
        (tmp_path / f"{name}.md").write_text(name)
    assert prune_stale(tmp_path, {"a.md", "b.md"}, "*.md").removed == ["c.md", "d.md"]   # exactly half: allowed
    (tmp_path / "c.md").write_text("c")
    (tmp_path / "d.md").write_text("d")
    assert prune_stale(tmp_path, {"a.md"}, "*.md").refused == ["b.md", "c.md", "d.md"]   # more than half: refused
