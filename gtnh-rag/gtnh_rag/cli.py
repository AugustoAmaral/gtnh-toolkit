"""gtnh-rag command line."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

from . import ask as ask_mod
from .config import Config, load_config
from .corpus import quest_chunks, recipe_chunks, vault_chunks, wiki_chunks
from .embed import OllamaEmbedder
from .fetch import fetch_vault, fetch_wiki, update_manifest
from .index import count_by_source, embed_missing, open_index, prune_missing, upsert_chunks
from .nerd import load_stacks, nerd_chunks
from .nesql import import_script
from .recipes import find_items, format_recipe, recipes_producing, recipes_using
from .search import search


def make_embedder(cfg: Config, model: str | None = None, url: str | None = None):
    return OllamaEmbedder(url or cfg.embed_url, model or cfg.embed_model)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def cmd_fetch(cfg: Config, args) -> int:
    failed_any = False
    if args.source in ("wiki", "all"):
        written, failed = fetch_wiki(cfg.wiki_api, cfg.raw_dir / "wiki")
        print(f"wiki: {written} pages written, {len(failed)} failed")
        update_manifest(cfg.raw_dir, {"wiki": {"api": cfg.wiki_api, "pages": written, "failed": failed}})
        failed_any |= bool(failed)
    if args.source in ("vault", "all"):
        if cfg.vault_dir is None:
            if args.source == "vault":
                print("vault: no vault_dir configured in gtnh_rag.toml, nothing to fetch", file=sys.stderr)
                return 1
            print("vault: no vault_dir configured, skipping")
        else:
            copied = fetch_vault(cfg.vault_dir, cfg.raw_dir / "vault")
            print(f"vault: {len(copied)} pages copied")
            update_manifest(cfg.raw_dir, {"vault": {"dir": str(cfg.vault_dir), "pages": len(copied)}})
    return 1 if failed_any else 0


def _ensure_recipes_db(cfg: Config, force: bool) -> sqlite3.Connection | None:
    script = cfg.raw_dir / "nesql" / "nesql-db.script"
    if not script.exists():
        print("warning: no nesql dump at", script, "- skipping recipes and quests")
        return None
    db = cfg.recipes_path
    digest_path = db.with_suffix(".sqlite.sha256")
    digest = _sha256(script)
    stale = force or not db.exists() or not digest_path.exists() or digest_path.read_text().strip() != digest
    if stale:
        print("importing nesql script into", db)
        rows = import_script(script, db)
        print(f"nesql: {rows} rows imported")
        digest_path.write_text(digest)
    return sqlite3.connect(db)


def cmd_build(cfg: Config, args) -> int:
    conn = open_index(cfg.index_path)
    all_ids: set[str] = set()
    walked_sources: set[str] = set()
    inserted_total = 0

    def add(name, chunks):
        nonlocal inserted_total
        walked_sources.add(name)
        batch = []
        count = 0
        for c in chunks:
            all_ids.add(c.id)
            batch.append(c)
            count += 1
            if len(batch) >= 2000:
                inserted_total += upsert_chunks(conn, batch)[0]
                batch = []
        if batch:
            inserted_total += upsert_chunks(conn, batch)[0]
        print(f"{name}: {count} chunks")

    wiki_dir = cfg.raw_dir / "wiki"
    if wiki_dir.exists():
        add("wiki", wiki_chunks(wiki_dir))
    vault_dir = cfg.raw_dir / "vault"
    if vault_dir.exists():
        add("vault", vault_chunks(vault_dir))
    if not args.skip_nesql:
        rconn = _ensure_recipes_db(cfg, force=False)
        if rconn is not None:
            add("quest", quest_chunks(rconn))
            add("recipe", recipe_chunks(rconn))
            rconn.close()
    nerd_json = cfg.raw_dir / "nerd" / "recipes.json"
    if not args.skip_nerd and nerd_json.exists() and cfg.nerd_handlers:
        stacks = load_stacks(cfg.raw_dir / "nerd" / "recipes_stacks.json")
        add("nei", nerd_chunks(nerd_json, stacks, set(cfg.nerd_handlers)))
    counts = count_by_source(conn)
    prune_sources = set(walked_sources)
    exit_code = 0
    for src in sorted(walked_sources):
        existing = counts.get(src, 0)
        if existing == 0:
            continue
        missing = sum(
            1 for (i,) in conn.execute("SELECT id FROM chunks WHERE source=?", (src,)) if i not in all_ids
        )
        if missing > 0 and missing > existing * 0.5 and not args.force_prune:
            print(
                f"refusing to prune {missing}/{existing} chunks of source '{src}' (raw input missing?); "
                "re-run with --force-prune to confirm",
                file=sys.stderr,
            )
            prune_sources.discard(src)
            exit_code = 1
    pruned = prune_missing(conn, all_ids, sources=prune_sources)
    total = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    print(f"index: {inserted_total} new chunks, {pruned} pruned, {total} total")
    if args.skip_embed:
        return exit_code
    embedder = make_embedder(cfg, args.embed_model, args.embed_url)
    done = embed_missing(conn, embedder, progress=lambda d, n: print(f"\rembedding {d}/{n}", end="", file=sys.stderr))
    print(f"\nembedded {done} chunks with {embedder.model}")
    return exit_code


def cmd_search(cfg: Config, args) -> int:
    if not cfg.index_path.exists():
        raise RuntimeError(f"no index at {cfg.index_path}; run `gtnh-rag build` first")
    conn = open_index(cfg.index_path)
    hits = search(conn, make_embedder(cfg), args.query, k=args.k)
    if args.json:
        print(json.dumps([asdict(h) for h in hits], ensure_ascii=False, indent=1))
    else:
        for i, h in enumerate(hits, start=1):
            print(f"[{i}] ({h.source}) {h.doc_title}" + (f" > {h.section}" if h.section else "") + f"  score={h.score:.4f}")
            print(h.text[:600].rstrip() + ("…" if len(h.text) > 600 else ""))
            print()
    return 0


def cmd_ask(cfg: Config, args) -> int:
    if not cfg.index_path.exists():
        raise RuntimeError(f"no index at {cfg.index_path}; run `gtnh-rag build` first")
    conn = open_index(cfg.index_path)
    ask_mod.ask(conn, make_embedder(cfg), cfg.chat_url, args.model or cfg.chat_model, cfg.chat_provider, args.query, k=args.k)
    return 0


def cmd_recipe(cfg: Config, args) -> int:
    if not cfg.recipes_path.exists():
        print("no recipes database; run `gtnh-rag build` first", file=sys.stderr)
        return 1
    conn = sqlite3.connect(cfg.recipes_path)
    items = find_items(conn, args.name)
    if not items:
        print(f"no item matches {args.name!r}")
        return 1
    printed = 0
    for item_id, name in items[: args.limit]:
        recipes = recipes_using(conn, item_id) if args.uses else recipes_producing(conn, item_id)
        if not recipes:
            continue
        printed += 1
        print(f"== {name}  ({'used in' if args.uses else 'made by'} {len(recipes)} recipes)  [{item_id}]")
        for r in recipes:
            print(f"  {r.type_name}: {format_recipe(r)}")
        if len(recipes) == 200:
            print("  (showing the first 200; more exist)")
        print()
    if printed == 0:
        suffix = " (--uses)" if args.uses else ""
        print(f"no recipes for {args.name!r}{suffix}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gtnh-rag", description="GTNH knowledge base: search, ask, recipes")
    p.add_argument("--config", default="gtnh_rag.toml")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download raw sources into data/raw")
    f.add_argument("--source", choices=["wiki", "vault", "all"], default="all")
    f.set_defaults(fn=cmd_fetch)

    b = sub.add_parser("build", help="build corpus, index and recipes.sqlite from data/raw")
    b.add_argument("--embed-model")
    b.add_argument("--embed-url")
    b.add_argument("--skip-embed", action="store_true")
    b.add_argument("--skip-nesql", action="store_true")
    b.add_argument("--skip-nerd", action="store_true")
    b.add_argument("--force-prune", action="store_true", help="allow pruning more than half of a source's existing chunks")
    b.set_defaults(fn=cmd_build)

    s = sub.add_parser("search", help="hybrid search, prints chunks")
    s.add_argument("query")
    s.add_argument("-k", type=int, default=10)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_search)

    a = sub.add_parser("ask", help="answer a question in pt-BR with citations")
    a.add_argument("query")
    a.add_argument("--model")
    a.add_argument("-k", type=int, default=8)
    a.set_defaults(fn=cmd_ask)

    r = sub.add_parser("recipe", help="exact recipe lookup by item name")
    r.add_argument("name")
    r.add_argument("--uses", action="store_true", help="recipes that consume the item instead of producing it")
    r.add_argument("--limit", type=int, default=5, help="max matching items to show")
    r.set_defaults(fn=cmd_recipe)

    args = p.parse_args(argv)
    cfg = load_config(Path(args.config))
    try:
        return args.fn(cfg, args)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
