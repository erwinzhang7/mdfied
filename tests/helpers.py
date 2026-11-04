"""Builders for small test inputs. Everything is generated; no real documents."""

import zipfile

NS = (
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
)
RELS = "http://schemas.openxmlformats.org/package/2006/relationships"
RT = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def text_box(text, y=0, ph=None):
    nvpr = f'<p:nvPr><p:ph type="{ph}"/></p:nvPr>' if ph else "<p:nvPr/>"
    paras = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in text.split("\n"))
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="1" name="s"/><p:cNvSpPr/>{nvpr}</p:nvSpPr>'
        f'<p:spPr><a:xfrm><a:off x="0" y="{y}"/><a:ext cx="1" cy="1"/></a:xfrm></p:spPr>'
        f"<p:txBody><a:bodyPr/>{paras}</p:txBody></p:sp>"
    )


def slide_xml(shapes, show=True):
    attr = "" if show else ' show="0"'
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><p:sld {NS}{attr}><p:cSld><p:spTree>'
        + "".join(shapes)
        + "</p:spTree></p:cSld></p:sld>"
    )


def pic(rid, descr=None):
    d = f' descr="{descr}"' if descr else ""
    return (
        f'<p:pic><p:nvPicPr><p:cNvPr id="2" name="p"{d}/><p:cNvPicPr/><p:nvPr/></p:nvPicPr>'
        f'<p:blipFill><a:blip r:embed="{rid}"/></p:blipFill><p:spPr/></p:pic>'
    )


def make_pptx(path, slides, order=None, absolute_targets=False, extra=None,
              slide_rels=None):
    """Write a minimal .pptx. ``slides`` is a list of slide XML strings, stored
    as slide1.xml.. in list order; ``order`` lists the indices in presentation
    order (default: list order)."""
    order = list(range(len(slides))) if order is None else order
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr(
            "_rels/.rels",
            f'<Relationships xmlns="{RELS}"><Relationship Id="rId1" '
            f'Type="{RT}/officeDocument" Target="ppt/presentation.xml"/></Relationships>',
        )
        ids = "".join(
            f'<p:sldId id="{256 + n}" r:id="rId{i + 10}"/>' for n, i in enumerate(order)
        )
        z.writestr(
            "ppt/presentation.xml",
            f'<p:presentation {NS}><p:sldIdLst>{ids}</p:sldIdLst></p:presentation>',
        )
        rels = "".join(
            f'<Relationship Id="rId{i + 10}" Type="{RT}/slide" Target="'
            + (f"/ppt/slides/slide{i + 1}.xml" if absolute_targets else f"slides/slide{i + 1}.xml")
            + '"/>'
            for i in range(len(slides))
        )
        z.writestr("ppt/_rels/presentation.xml.rels",
                   f'<Relationships xmlns="{RELS}">{rels}</Relationships>')
        for i, xml in enumerate(slides):
            z.writestr(f"ppt/slides/slide{i + 1}.xml", xml)
            rels = (slide_rels or {}).get(i)
            if rels:
                body = "".join(
                    f'<Relationship Id="{rid}" Type="{RT}/image" Target="{t}"/>'
                    for rid, t in rels
                )
                z.writestr(f"ppt/slides/_rels/slide{i + 1}.xml.rels",
                           f'<Relationships xmlns="{RELS}">{body}</Relationships>')
        for name, data in (extra or {}).items():
            z.writestr(name, data)


def simple_pptx(path, title="Deck"):
    make_pptx(path, [slide_xml([text_box(title, y=0, ph="title"), text_box("body", y=100)])])


def make_pdf(path, text="Hello"):
    from mdfied.pdf import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text, fontsize=20)
    doc.save(str(path))
    doc.close()


def png_bytes(w=64, h=64, rgb=(200, 30, 30)):
    """A real PNG of a solid colour, built without third-party libraries."""
    import struct
    import zlib

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    row = b"\x00" + bytes(rgb) * w
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * h)) + chunk(b"IEND", b""))


def make_image_pdf(path):
    """Page 1: a CMYK image, an RGBA image, a 4x4 dot. Page 2: the CMYK image
    again through the same object."""
    from mdfied.pdf import fitz

    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((72, 40), "Images", fontsize=20)
    cmyk = fitz.Pixmap(fitz.csCMYK, fitz.IRect(0, 0, 80, 80), False)
    cmyk.clear_with(90)
    x_cmyk = p.insert_image(fitz.Rect(0, 100, 80, 180), pixmap=cmyk)
    rgba = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 80, 60), True)
    rgba.clear_with(180)
    p.insert_image(fitz.Rect(100, 100, 180, 160), pixmap=rgba)
    dot = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 4, 4), False)
    dot.clear_with(0)
    p.insert_image(fitz.Rect(0, 200, 4, 204), pixmap=dot)
    p2 = doc.new_page()
    p2.insert_image(fitz.Rect(0, 0, 80, 80), xref=x_cmyk)
    doc.save(str(path))
    doc.close()
