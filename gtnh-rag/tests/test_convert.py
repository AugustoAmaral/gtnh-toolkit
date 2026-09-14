from pathlib import Path

from gtnh_rag.convert import wikitext_to_markdown

FIX = Path(__file__).parent / "fixtures" / "sample.wikitext"


def test_headings_become_markdown_headings():
    md = wikitext_to_markdown(FIX.read_text())
    assert "\n## Mechanics\n" in md
    assert "\n### Tier\n" in md


def test_links_bold_italic_and_templates():
    md = wikitext_to_markdown(FIX.read_text())
    assert "**CropsNH** is an Upcoming Feature for 2.9. See Industrial Farm." in md
    assert "*crop sticks*" in md
    assert "{{NYI" not in md
    assert "<math>S = 6 + Gr</math>" in md  # kept verbatim


def test_tables_become_pipe_rows():
    md = wikitext_to_markdown(FIX.read_text())
    assert "| Tier | Note |" in md
    assert "| 4 | grows anywhere |" in md
    assert "| 5 | needs sky |" in md


def test_files_and_categories_dropped():
    md = wikitext_to_markdown(FIX.read_text())
    assert "File:" not in md
    assert "Category:" not in md


def test_multiline_table_cells():
    wikitext = """{|
|-
| 4 || grows anywhere
and keeps growing
|}"""
    md = wikitext_to_markdown(wikitext)
    assert "| 4 | grows anywhere and keeps growing |" in md


def test_nested_file_link_with_caption():
    wikitext = "[[File:Foo.png|thumb|See [[Bar]] for more]] tail"
    md = wikitext_to_markdown(wikitext)
    assert "File:" not in md
    assert "tail" in md
    assert "for more" not in md


def test_deep_template_nesting():
    wikitext = "x {{a|{{b|{{c|{{d}}}}}}}} y"
    md = wikitext_to_markdown(wikitext)
    assert "{{" not in md
    assert "x" in md
    assert "y" in md


def test_infobox_tier_and_checkmark_templates():
    md = wikitext_to_markdown(FIX.read_text())
    assert "Infobox Multiblock:" in md
    assert "tier: IV" in md
    assert "size: 7 x 7 x 4" in md
    assert "pollution:" not in md
    assert "Can be bred at HV but requires EV." in md
    assert "yes" in md
    assert "Any casing" in md
