from gtnh_rag.chunk import Chunk, split_sections


def test_chunk_id_is_stable_and_16_hex():
    a = Chunk("wiki", "LFTR", "Usage", "text")
    b = Chunk("wiki", "LFTR", "Usage", "text")
    assert a.id == b.id
    assert len(a.id) == 16
    assert int(a.id, 16) >= 0
    assert Chunk("wiki", "LFTR", "Usage", "other").id != a.id


def test_breadcrumb():
    assert Chunk("wiki", "LFTR", "Usage", "x").breadcrumb == "LFTR > Usage"
    assert Chunk("wiki", "LFTR", "", "x").breadcrumb == "LFTR"


def test_split_by_headings_prepends_breadcrumb():
    md = "Intro line.\n\n## Construction\nNeeds casings.\n\n## Usage\nRuns on fuel.\n"
    chunks = split_sections(md, "LFTR", "wiki")
    assert [c.section for c in chunks] == ["", "Construction", "Usage"]
    assert chunks[1].text.startswith("LFTR > Construction\n")
    assert "Needs casings." in chunks[1].text
    assert all(c.source == "wiki" and c.doc_title == "LFTR" for c in chunks)


def test_long_section_splits_on_paragraphs_and_keeps_breadcrumb():
    paras = "\n\n".join(f"Paragraph {i} " + "x" * 300 for i in range(10))
    md = "## Big\n" + paras
    chunks = split_sections(md, "Doc", "wiki", max_chars=1000)
    assert len(chunks) > 1
    assert all(c.section == "Big" for c in chunks)
    assert all(c.text.startswith("Doc > Big\n") for c in chunks)
    assert all(len(c.text) <= 1000 + len("Doc > Big\n") + 320 for c in chunks)  # one paragraph may overflow


def test_empty_sections_are_dropped():
    md = "## A\n\n## B\nreal text\n"
    chunks = split_sections(md, "Doc", "wiki")
    assert [c.section for c in chunks] == ["B"]
