import os


from helpers import make_image_pdf, make_pptx, pic, png_bytes, slide_xml, text_box

from mdfied import pptx as pptx_mod
from mdfied.pdf import convert_pdf, fitz
from mdfied.pptx import convert_pptx


def files(d):
    return sorted(os.listdir(d)) if os.path.isdir(d) else []


# --- PDF --------------------------------------------------------------------

def test_pdf_images_extracted_converted_and_deduped(tmp_path):
    src = tmp_path / "scan.pdf"
    make_image_pdf(src)
    out = tmp_path / "o" / "scan.md"
    n, links = convert_pdf(str(src), str(out))
    assert n == 2
    assert links == ["scan_files/p1-1.png", "scan_files/p1-2.png"]  # dot skipped
    md = out.read_text()
    # The CMYK image is referenced on both pages but stored once.
    assert md.count("(scan_files/p1-1.png)") == 2
    assert "![image on page 2](scan_files/p1-1.png)" in md

    cmyk = fitz.Pixmap(str(tmp_path / "o" / "scan_files" / "p1-1.png"))
    assert cmyk.n == 3 and not cmyk.alpha           # CMYK became RGB
    rgba = fitz.Pixmap(str(tmp_path / "o" / "scan_files" / "p1-2.png"))
    assert rgba.alpha and (rgba.width, rgba.height) == (80, 60)  # soft mask kept


def test_pdf_all_images_keeps_tiny(tmp_path):
    src = tmp_path / "s.pdf"
    make_image_pdf(src)
    _, links = convert_pdf(str(src), str(tmp_path / "s.md"), all_images=True)
    assert len(links) == 3


def test_pdf_no_images_is_reference_only(tmp_path):
    src = tmp_path / "s.pdf"
    make_image_pdf(src)
    n, links = convert_pdf(str(src), str(tmp_path / "s.md"), images=False)
    assert links == [] and not (tmp_path / "s_files").exists()
    assert "[IMAGE: img_" in (tmp_path / "s.md").read_text()


def test_links_are_url_encoded(tmp_path):
    src = tmp_path / "Unit 1 (draft).pdf"
    make_image_pdf(src)
    out = tmp_path / "Unit 1 (draft).md"
    convert_pdf(str(src), str(out))
    md = out.read_text()
    assert "(Unit%201%20%28draft%29_files/p1-1.png)" in md
    assert (tmp_path / "Unit 1 (draft)_files" / "p1-1.png").is_file()


# --- PPTX -------------------------------------------------------------------

def deck(path, slides_shapes, rels, media):
    make_pptx(
        path,
        [slide_xml(shapes) for shapes in slides_shapes],
        slide_rels=rels,
        extra=media,
    )


def test_pptx_media_copied_once_with_original_format(tmp_path):
    src = tmp_path / "d.pptx"
    gif = b"GIF89a" + b"\x00" * 20
    deck(src,
         [[text_box("A", ph="title"), pic("rId1", "a chart"), pic("rId2")],
          [text_box("B", ph="title"), pic("rId1")]],
         {0: [("rId1", "../media/image1.png"), ("rId2", "../media/anim.gif")],
          1: [("rId1", "../media/image1.png")]},
         {"ppt/media/image1.png": png_bytes(), "ppt/media/anim.gif": gif})
    out = tmp_path / "o" / "d.md"
    n, links = convert_pptx(str(src), str(out))
    assert links == ["d_files/image1.png", "d_files/anim.gif"]
    md = out.read_text()
    assert "![a chart](d_files/image1.png)" in md
    assert "![image on slide 2](d_files/image1.png)" in md
    assert (tmp_path / "o" / "d_files" / "anim.gif").read_bytes() == gif


def test_pptx_refuses_unsafe_or_unsupported_media(tmp_path, monkeypatch):
    src = tmp_path / "d.pptm"
    deck(src,
         [[pic("rId1"), pic("rId2"), pic("rId3"), pic("rId4"), pic("rId5"), pic("rId6")]],
         {0: [("rId1", "../media/fake.png"),        # not really a PNG
              ("rId2", "../media/image2.emf"),      # vector format, not copied
              ("rId3", "../../../../etc/evil.png"), # escapes the package
              ("rId4", "../vbaProject.bin"),        # macros are never read
              ("rId5", "../media/big.png"),         # over the size cap
              ("rId6", "../media/ok.png")]},
         {"ppt/media/fake.png": b"MZ\x90\x00 not an image",
          "ppt/media/image2.emf": b"\x01\x00\x00\x00",
          "ppt/vbaProject.bin": b"Attribute VB_Name",
          "ppt/media/big.png": png_bytes(300, 300) + b"\x00" * 4000,
          "ppt/media/ok.png": png_bytes(8, 8)})
    monkeypatch.setattr(pptx_mod, "MAX_MEDIA_BYTES", 2000)
    out_dir = tmp_path / "o"
    out = out_dir / "d.md"
    _, links = convert_pptx(str(src), str(out))
    assert links == ["d_files/ok.png"]
    md = out.read_text()
    assert "fake.png not copied (content does not match its extension)" in md
    assert "image2.emf not copied (unsupported format)" in md
    assert "evil.png not copied (missing from the file)" in md
    assert "vbaProject.bin not copied (unsupported format)" in md
    assert "big.png not copied (too large)" in md
    written = [os.path.relpath(os.path.join(r, f), tmp_path)
               for r, _d, fs in os.walk(tmp_path) for f in fs]
    assert sorted(written) == sorted(["d.pptm", "o/d.md", "o/d_files/ok.png"])


def test_pptx_hostile_member_names_stay_in_folder(tmp_path):
    src = tmp_path / "d.pptx"
    deck(src, [[pic("rId1")]],
         {0: [("rId1", "../media/..%2F..%2Fx .png")]},
         {"ppt/media/..%2F..%2Fx .png": png_bytes(8, 8)})
    _, links = convert_pptx(str(src), str(tmp_path / "o" / "d.md"))
    assert len(links) == 1 and links[0].startswith("d_files/")
    assert "/" not in links[0][len("d_files/"):]
    assert files(tmp_path / "o" / "d_files") == [links[0].split("/")[1]]
