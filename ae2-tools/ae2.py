#!/usr/bin/env python3
"""Single entry point wrapping every tool in this toolkit. `python3 ae2.py <subcommand> --help`
shows that tool's own full help (same flags, same behaviour — this only saves remembering eleven
filenames). Dependency-light: stdlib + nbtlib 2.0.4 (+ numpy, nbtlib's own dependency) for anything
that touches NBT, sqlite3 (stdlib) for recipes.sqlite. Start at `audit`.

Subcommands -> script:
    audit          audit.py              ONE COMMAND: scan -> string identities -> verdicts -> coverage
    diff-audits    diff_audits.py        diff two audit dirs by pattern identity (not by scan index)
    export         export_patterns.py    every pattern as string identities (json + one-line-each txt)
    dump           dump_tes.py           dump every TileEntity of a region dir to JSON.gz
    scan           scan_patterns.py      scan region dirs (+ playerdata) for AE2 encoded patterns
    idmap          idmaps.py             diff FML numeric item/block id registries across level.dat
    interfaces     build_interfaces.py   inventory + name-proposal for every ME Interface
    review         review_matcher.py     match every pattern against recipes.sqlite -> verdict
    gemma-review   gemma_review.py       optional second opinion (LLM) on review_matcher.py's residue;
                                         NOT part of `audit` and shown unreliable (see README)
    apply-names    apply_interface_names.py   write proposed interface names into region files
    apply-patterns apply_pattern_fix.py       write pattern fixes into region files (delete/replace)

Every subcommand forwards its remaining args verbatim to the target script's own argparse parser,
so `ae2.py dump --help` is byte-identical to `python3 dump_tes.py --help`.
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

SUBCOMMANDS = {
    "audit": "audit.py",
    "diff-audits": "diff_audits.py",
    "export": "export_patterns.py",
    "dump": "dump_tes.py",
    "scan": "scan_patterns.py",
    "idmap": "idmaps.py",
    "interfaces": "build_interfaces.py",
    "review": "review_matcher.py",
    "gemma-review": "gemma_review.py",
    "apply-names": "apply_interface_names.py",
    "apply-patterns": "apply_pattern_fix.py",
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 0
    sub = sys.argv[1]
    script = SUBCOMMANDS.get(sub)
    if script is None:
        print(f"unknown subcommand {sub!r}. Known: {', '.join(SUBCOMMANDS)}", file=sys.stderr)
        return 2
    path = os.path.join(HERE, script)
    if not os.path.exists(path):
        print(f"{script} not found next to ae2.py ({path})", file=sys.stderr)
        return 2
    sys.argv = [path] + sys.argv[2:]
    runpy.run_path(path, run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
