import json
from pathlib import Path

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
    written, failed = fetch_wiki("http://x/api.php", tmp_path, session=FakeSession())
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
    copied = fetch_vault(vault, out)
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
    written, failed = fetch_wiki("http://x/api.php", tmp_path, session=FakeSession())
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
