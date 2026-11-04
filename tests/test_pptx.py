import io
import struct
import zlib

import pytest

from helpers import NS, make_pptx, slide_xml, text_box

from mdfied import pptx as pptx_mod
from mdfied.pptx import pptx_to_markdown


def convert(tmp_path, src):
    out = tmp_path / "out.md"
    n = pptx_to_markdown(str(src), None, out_path=str(out))
    return n, out.read_text(encoding="utf-8")


def tiny_png():
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


def test_python_pptx_deck(tmp_path):
    pptx = pytest.importorskip("pptx")
    from pptx.util import Inches

    prs = pptx.Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = "Deck Title"
    s.placeholders[1].text = "A subtitle"

    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "Bullets"
    tf = s.placeholders[1].text_frame
    tf.text = "first"
    p = tf.add_paragraph()
    p.text = "nested"
    p.level = 1
    s.notes_slide.notes_text_frame.text = "say this out loud"

    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "Table"
    t = s.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(4), Inches(1)).table
    for (r, c), v in {(0, 0): "h1", (0, 1): "h|2", (1, 0): "a", (1, 1): "b"}.items():
        t.cell(r, c).text = v
    s.shapes.add_picture(io.BytesIO(tiny_png()), Inches(1), Inches(4))
    g = s.shapes.add_group_shape()
    g.shapes.add_textbox(Inches(5), Inches(5), Inches(1), Inches(1)).text_frame.text = "grouped"

    # Move the table slide to the front: order must follow sldIdLst.
    lst = prs.slides._sldIdLst
    ids = list(lst)
    lst.remove(ids[2])
    lst.insert(0, ids[2])
    src = tmp_path / "deck.pptx"
    prs.save(str(src))

    n, md = convert(tmp_path, src)
    assert n == 3
    assert md == (
        "# deck\n\n"
        "## Slide 1 — Table\n\n"
        "| h1 | h\\|2 |\n|---|---|\n| a | b |\n\n"
        "grouped\n\n"
        "[IMAGE: image1.png — image on slide 1]\n\n"
        "## Slide 2 — Deck Title\n\n"
        "A subtitle\n\n"
        "## Slide 3 — Bullets\n\n"
        "- first\n  - nested\n\n"
        "### Notes\n\n"
        "say this out loud\n"
    )


def test_order_absolute_targets_hidden_and_fallback_title(tmp_path):
    slides = [
        slide_xml([text_box("second", y=0, ph="title")]),
        # No placeholders (as in Google Slides exports): topmost single-line
        # text box becomes the title.
        slide_xml([text_box("body text", y=500), text_box("Real Title", y=10)], show=False),
        # A row of boxes at the same height is not a title.
        slide_xml([text_box("1", y=0), text_box("Item", y=0)]),
    ]
    src = tmp_path / "d.pptx"
    make_pptx(src, slides, order=[1, 0, 2], absolute_targets=True)
    n, md = convert(tmp_path, src)
    assert n == 3
    assert md.index("## Slide 1 — Real Title") < md.index("## Slide 2 — second")
    assert "*(hidden slide)*" in md
    assert "## Slide 3\n\n1\n\nItem" in md
    assert "## Slide 1 — Real Title\n\n*(hidden slide)*\n\nbody text" in md


def test_footer_placeholders_skipped(tmp_path):
    src = tmp_path / "d.pptx"
    make_pptx(src, [slide_xml([
        text_box("T", ph="title"), text_box("7", ph="sldNum"),
        text_box("Jan 1", ph="dt"), text_box("Footer", ph="ftr"), text_box("keep", y=9),
    ])])
    _, md = convert(tmp_path, src)
    assert md == "# d\n\n## Slide 1 — T\n\nkeep\n"


def test_empty_deck(tmp_path):
    src = tmp_path / "e.pptx"
    make_pptx(src, [])
    assert convert(tmp_path, src) == (0, "# e\n\n\n")


def test_missing_slide_part(tmp_path):
    import zipfile

    full = tmp_path / "full.pptx"
    make_pptx(full, [slide_xml([text_box("one", ph="title")]),
                     slide_xml([text_box("two", ph="title")])])
    src = tmp_path / "m.pptx"
    with zipfile.ZipFile(full) as zin, zipfile.ZipFile(src, "w") as zout:
        for item in zin.infolist():
            if item.filename != "ppt/slides/slide1.xml":
                zout.writestr(item, zin.read(item))
    n, md = convert(tmp_path, src)
    assert n == 2
    assert "## Slide 1\n\n*(slide part missing: ppt/slides/slide1.xml)*" in md
    assert "## Slide 2 — two" in md


def test_entity_expansion_rejected(tmp_path):
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
        f'<p:sld {NS}><p:cSld><p:spTree>&lol2;</p:spTree></p:cSld></p:sld>'
    )
    src = tmp_path / "b.pptx"
    make_pptx(src, [bomb])
    with pytest.raises(Exception) as e:
        convert(tmp_path, src)
    assert "Entit" in type(e.value).__name__


def test_external_entity_rejected(tmp_path):
    xxe = (
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
        f'<p:sld {NS}><p:cSld><p:spTree>&e;</p:spTree></p:cSld></p:sld>'
    )
    src = tmp_path / "x.pptx"
    make_pptx(src, [xxe])
    with pytest.raises(Exception):
        convert(tmp_path, src)


def test_oversized_part_rejected(tmp_path, monkeypatch):
    src = tmp_path / "big.pptx"
    make_pptx(src, [slide_xml([text_box("x" * 5000)])])
    monkeypatch.setattr(pptx_mod, "MAX_PART_BYTES", 1000)
    with pytest.raises(ValueError, match="exceeds"):
        convert(tmp_path, src)


def test_default_output_name(tmp_path):
    src = tmp_path / "Unit 1 Lecture.pptx"
    make_pptx(src, [slide_xml([text_box("Hi", ph="title")])])
    pptx_to_markdown(str(src), str(tmp_path / "o"))
    assert (tmp_path / "o" / "Unit 1 Lecture.md").read_text().startswith("# Unit 1 Lecture\n")
