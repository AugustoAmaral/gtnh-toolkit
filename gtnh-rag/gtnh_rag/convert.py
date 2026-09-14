"""Light, deterministic wikitext -> markdown for retrieval (not rendering)."""
from __future__ import annotations

import re

_TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
_FILE_LINK = re.compile(r"\[\[(?:File|Image|Category):(?:[^\[\]]|\[\[[^\]]*\]\])*\]\]", re.IGNORECASE)
_LINK = re.compile(r"\[\[([^\]|]*)(?:\|([^\]]*))?\]\]")
_EXT_LINK = re.compile(r"\[(https?://\S+)\s+([^\]]+)\]")
_HEADING = re.compile(r"^(={1,6})\s*(.*?)\s*\1\s*$")

_COLOR = re.compile(r"\{\{Color\|[^|}]*\|([^}]*)\}\}", re.IGNORECASE)
_TIER = re.compile(r"\{\{(ULV|LV|MV|HV|EV|IV|LuV|ZPM|UV|UHV|UEV|UIV|UMV|UXV|MAX)\}\}")
_INFOBOX = re.compile(r"\{\{(Infobox\s+[^|{}]+?)\s*(\|.*?)?\}\}", re.IGNORECASE | re.DOTALL)


def _split_infobox_fields(body: str) -> list[str]:
    """Split Infobox params on top-level '|', respecting [[...]] link boundaries."""
    fields: list[str] = []
    current: list[str] = []
    depth = 0
    i = 0
    while i < len(body):
        if body[i:i + 2] == "[[":
            depth += 1
            current.append("[[")
            i += 2
        elif body[i:i + 2] == "]]":
            depth = max(0, depth - 1)
            current.append("]]")
            i += 2
        elif body[i] == "|" and depth == 0:
            fields.append("".join(current))
            current = []
            i += 1
        else:
            current.append(body[i])
            i += 1
    fields.append("".join(current))
    return fields


def _infobox_repl(m: re.Match) -> str:
    name = m.group(1).strip()
    rest = m.group(2) or ""
    if rest.startswith("|"):
        rest = rest[1:]
    lines = [f"{name}:"]
    for field in _split_infobox_fields(rest) if rest else []:
        if "=" not in field:
            continue
        k, v = field.split("=", 1)
        k, v = k.strip(), v.strip()
        if v:
            lines.append(f"{k}: {v}")
    return "\n".join(lines)


def _convert_table(lines: list[str]) -> list[str]:
    """Turn wikitable lines (between {| and |}) into pipe rows."""
    rows: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        s = line.strip()
        if s.startswith("|-") or s.startswith("|+"):
            if current:
                rows.append(current)
            current = []
        elif s.startswith("!") or s.startswith("|"):
            body = s[1:]
            cells = re.split(r"\s*(?:!!|\|\|)\s*", body)
            for cell in cells:
                cell = cell.split("|")[-1].strip() if "style=" in cell or "colspan=" in cell or "rowspan=" in cell else cell.strip()
                current.append(cell)
        elif s and not s.startswith("|}"):
            # Continuation line: append to last cell with a space
            if current:
                current[-1] += " " + s
            else:
                current.append(s)
    if current:
        rows.append(current)
    return ["| " + " | ".join(r) + " |" for r in rows if any(r)]


def wikitext_to_markdown(text: str) -> str:
    # Templates that carry meaningful text: keep it before the generic drop below.
    text = _COLOR.sub(lambda m: m.group(1), text)
    text = _TIER.sub(lambda m: m.group(1), text)
    text = text.replace("{{✓}}", "yes").replace("{{✗}}", "no")
    text = _INFOBOX.sub(_infobox_repl, text)

    # Drop templates (loop until no more changes, max 10 passes).
    for _ in range(10):
        new_text = _TEMPLATE.sub("", text)
        if new_text == text:
            break
        text = new_text
    text = _FILE_LINK.sub("", text)
    text = _LINK.sub(lambda m: m.group(2) if m.group(2) is not None else m.group(1), text)
    text = _EXT_LINK.sub(lambda m: m.group(2), text)
    text = text.replace("'''", "**").replace("''", "*")

    out: list[str] = []
    table: list[str] | None = None
    for line in text.splitlines():
        s = line.strip()
        if table is not None:
            if s.startswith("|}"):
                out.extend(_convert_table(table))
                table = None
            else:
                table.append(line)
            continue
        if s.startswith("{|"):
            table = []
            continue
        m = _HEADING.match(s)
        if m:
            out.append("")
            out.append("#" * len(m.group(1)) + " " + m.group(2))
            continue
        out.append(line.rstrip())
    if table:
        out.extend(_convert_table(table))
    md = "\n".join(out)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip() + "\n"
