"""Download raw sources: GTNH wiki (MediaWiki API) and optional local markdown notes."""
from __future__ import annotations

import json
import re
import shutil
import time
from datetime import date
from pathlib import Path

import requests

USER_AGENT = "gtnh-rag/0.1 (https://github.com/AugustoAmaral/gtnh-toolkit)"
REQUEST_DELAY = 0.2
_TAGS_LINE = re.compile(r"^tags:\s*\[(.*?)\]", re.MULTILINE)


def safe_name(title: str) -> str:
    return title.replace("/", "__").replace("\\", "__").replace(":", "_")


def _session(session):
    if session is not None:
        return session
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def list_all_pages(api_url: str, session=None) -> list[str]:
    s = _session(session)
    titles: list[str] = []
    params = {"action": "query", "list": "allpages", "apnamespace": 0, "aplimit": "max", "format": "json"}
    while True:
        resp = s.get(api_url, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        titles.extend(p["title"] for p in data["query"]["allpages"])
        cont = data.get("continue")
        if not cont:
            return titles
        params = {**params, **cont}
        time.sleep(REQUEST_DELAY)


def fetch_page(api_url: str, title: str, session=None) -> str:
    s = _session(session)
    params = {"action": "query", "prop": "revisions", "rvprop": "content", "rvslots": "main", "titles": title, "format": "json"}
    resp = s.get(api_url, params=params, timeout=60)
    resp.raise_for_status()
    pages = resp.json()["query"]["pages"]
    page = next(iter(pages.values()))
    if "missing" in page or "revisions" not in page:
        raise LookupError(f"page not found: {title}")
    return page["revisions"][0]["slots"]["main"]["*"]


def fetch_wiki(api_url: str, out_dir: Path, session=None) -> tuple[int, list[str]]:
    s = _session(session)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written, failed = 0, []
    for title in list_all_pages(api_url, s):
        try:
            text = fetch_page(api_url, title, s)
        except Exception as exc:  # network or missing page: log and continue
            print(f"[fetch] failed {title!r}: {exc}")
            failed.append(title)
        else:
            (out_dir / f"{safe_name(title)}.wikitext").write_text(text, encoding="utf-8")
            written += 1
        finally:
            time.sleep(REQUEST_DELAY)
    return written, failed


def _is_gtnh_page(path: Path) -> bool:
    if "gtnh" in path.as_posix().lower():
        return True
    head = path.read_text(encoding="utf-8", errors="ignore")[:2000]
    m = _TAGS_LINE.search(head)
    return bool(m and "gtnh" in [t.strip().strip("'\"") for t in m.group(1).split(",")])


def fetch_vault(vault_dir: Path, out_dir: Path) -> list[Path]:
    vault_dir, out_dir = Path(vault_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for sub in ("wiki", "frentes"):
        for p in sorted((vault_dir / sub).rglob("*.md")):
            if _is_gtnh_page(p):
                rel = p.relative_to(vault_dir).as_posix().replace("/", "__")
                shutil.copyfile(p, out_dir / rel)
                copied.append(p)
    return copied


def update_manifest(raw_dir: Path, entries: dict[str, dict]) -> None:
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / "manifest.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    for key, value in entries.items():
        data[key] = {"fetched_at": date.today().isoformat(), **value}
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False))
