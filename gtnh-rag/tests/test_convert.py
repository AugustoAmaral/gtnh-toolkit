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


def test_infobox_keeps_fields_after_nested_template():
    wikitext = "{{Infobox Multiblock|name=LFTR|tier={{TIER|HV}}|size=7 x 7 x 4|eut=2048}}\nBody text."
    md = wikitext_to_markdown(wikitext)
    assert md == "Infobox Multiblock:\nname: LFTR\ntier:\nsize: 7 x 7 x 4\neut: 2048\nBody text.\n"


def test_multiline_infobox_with_deeply_nested_template():
    wikitext = (
        "{{Infobox Machine\n|name=Assembler\n|recipe={{Recipe|in={{Item|Steel Plate}}|out=Hull}}\n"
        "|voltage=128\n}}\n== Usage ==\nPut plates in."
    )
    md = wikitext_to_markdown(wikitext)
    assert "voltage: 128" in md
    assert "in:" not in md and "out:" not in md  # params of the nested template are not infobox fields
    assert md.endswith("## Usage\nPut plates in.\n")


def test_nested_template_does_not_swallow_the_next_infobox():
    md = wikitext_to_markdown("{{Infobox A|x={{TIER|EV}}|y=1}} and {{Infobox B|z=2}} end")
    assert "y: 1" in md and "Infobox B:" in md and "z: 2" in md


def test_nested_template_next_to_links_in_infobox():
    md = wikitext_to_markdown("{{Infobox Item|a=[[Thorium|Th]] {{TIER|IV}}|b=[[Fluid|fl]]|c=3}}")
    assert md == "Infobox Item:\na: Th\nb: fl\nc: 3\n"


def test_infobox_without_nested_templates_converts_as_before():
    # Output of the converter before nested-template support; must not change.
    wikitext = (
        "{{Infobox Multiblock\n|name=LFTR\n|tier={{IV}}\n|casing={{Color|gray|Any casing}}\n"
        "|link=[[Thorium Dust|Th]]\n|empty=\n}}\nAfter."
    )
    md = wikitext_to_markdown(wikitext)
    assert md == "Infobox Multiblock:\nname: LFTR\ntier: IV\ncasing: Any casing\nlink: Th\nAfter.\n"
    assert wikitext_to_markdown("{{Infobox Foo}}") == "Infobox Foo:\n"


def test_unclosed_infobox_is_left_alone():
    wikitext = "{{Infobox A|a={{TIER|HV|b=2\nBody."
    assert wikitext_to_markdown(wikitext) == wikitext + "\n"
