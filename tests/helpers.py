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


def make_pptx(path, slides, order=None, absolute_targets=False, extra=None):
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
