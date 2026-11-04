"""
pptx.py — Extract per-slide text, tables, notes and image references from
PowerPoint files into Markdown.

For each .pptx, the slides are walked in presentation order (the slide-id list in
presentation.xml, resolved through its relationships, not filename order). The
slide title is the title/ctrTitle placeholder; decks without placeholders (Google
Slides exports, generated decks) fall back to the topmost single-line text box.
Every other text shape becomes body text in reading order, tables become
Markdown tables, pictures are noted as image references, and speaker notes are
appended under `### Notes`. One Markdown file is written per deck:
lecture.pptx -> lecture.md.

Works on any .pptx — there is no filename convention. Slide sections are emitted
as `## Slide N — Title`, mirroring the PDF extractor's `## Page N — Title`.

A .pptx is a zip of XML parts. Parts are parsed with defusedxml (no entity
expansion, no external entities), each part's decompressed size is capped, and
nothing is extracted to disk.
"""

import os
import posixpath
import zipfile

from defusedxml import ElementTree as ET


# OOXML namespaces used inside a .pptx package.
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
RELNS = "http://schemas.openxmlformats.org/package/2006/relationships"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
C = "http://schemas.openxmlformats.org/drawingml/2006/chart"

REL_OFFICE_DOC = "/officeDocument"
REL_NOTES = "/notesSlide"

# Upper bound on one decompressed XML part. Real slide parts are kilobytes; the
# cap stops a crafted zip from expanding into memory.
MAX_PART_BYTES = 64 * 1024 * 1024

# Placeholders that repeat on every slide and carry no content.
SKIP_PLACEHOLDERS = {"sldNum", "dt", "ftr", "hdr", "sldImg"}
TITLE_PLACEHOLDERS = {"title", "ctrTitle"}
BODY_PLACEHOLDERS = {"body", "obj"}

# Fallback titles longer than this are body text, not a heading.
MAX_FALLBACK_TITLE = 150


def _q(ns, tag):
    return f"{{{ns}}}{tag}"


def _read_xml(z, name):
    """Parse one package part, refusing parts that decompress past the cap."""
    with z.open(name) as f:
        data = f.read(MAX_PART_BYTES + 1)
    if len(data) > MAX_PART_BYTES:
        raise ValueError(f"part '{name}' exceeds {MAX_PART_BYTES} bytes")
    return ET.fromstring(data)


def _rels_name(part):
    d, base = posixpath.split(part)
    return posixpath.join(d, "_rels", base + ".rels")


def _resolve(part, target):
    """Resolve a relationship target relative to the part that owns it."""
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(part), target))


def _rels(z, part):
    """Return {rId: (type, resolved target)} for a part; {} if it has none."""
    name = _rels_name(part)
    try:
        root = _read_xml(z, name)
    except KeyError:
        return {}
    out = {}
    for r in root.findall(_q(RELNS, "Relationship")):
        target = r.get("Target") or ""
        if r.get("TargetMode") == "External":
            resolved = target
        else:
            resolved = _resolve(part, target)
        out[r.get("Id")] = (r.get("Type") or "", resolved)
    return out


def _main_part(z):
    for _rid, (rtype, target) in _rels(z, "").items():
        if rtype.endswith(REL_OFFICE_DOC):
            return target
    return "ppt/presentation.xml"


def _slide_order(z):
    """Return slide part names in presentation order."""
    pres_part = _main_part(z)
    pres = _read_xml(z, pres_part)
    rels = _rels(z, pres_part)
    lst = pres.find(_q(P, "sldIdLst"))
    if lst is None:
        return []
    ordered = []
    for sld_id in lst.findall(_q(P, "sldId")):
        rel = rels.get(sld_id.get(_q(R, "id")))
        if rel:
            ordered.append(rel[1])
    return ordered


def _paragraph_text(p):
    """Text of one a:p, with a:br as a newline."""
    out = []
    for el in p:
        if el.tag in (_q(A, "r"), _q(A, "fld")):
            t = el.find(_q(A, "t"))
            if t is not None and t.text:
                out.append(t.text)
        elif el.tag == _q(A, "br"):
            out.append("\n")
    return "".join(out).strip()


def _placeholder_type(sp):
    """Return a shape's placeholder type, or None if it is not a placeholder."""
    ph = sp.find(f"{_q(P, 'nvSpPr')}/{_q(P, 'nvPr')}/{_q(P, 'ph')}")
    if ph is None:
        return None
    return ph.get("type", "obj")  # the spec default for a typeless <p:ph>


def _shape_lines(sp, bulleted_default):
    """Return the shape's paragraphs as Markdown lines (bullets where marked)."""
    tx = sp.find(_q(P, "txBody"))
    if tx is None:
        return []
    lines = []
    for p in tx.findall(_q(A, "p")):
        text = _paragraph_text(p)
        if not text:
            continue
        ppr = p.find(_q(A, "pPr"))
        lvl = int(ppr.get("lvl", "0")) if ppr is not None else 0
        has_bullet = ppr is not None and (
            ppr.find(_q(A, "buChar")) is not None
            or ppr.find(_q(A, "buAutoNum")) is not None
        )
        no_bullet = ppr is not None and ppr.find(_q(A, "buNone")) is not None
        if has_bullet or (bulleted_default and not no_bullet):
            indent = "  " * lvl
            text = text.replace("\n", "\n" + indent + "  ")
            lines.append(f"{indent}- {text}")
        else:
            lines.append(text)
    return lines


def _offset(el):
    """(y, x) of a top-level shape, or None if it inherits its position."""
    off = el.find(f".//{_q(A, 'xfrm')}/{_q(A, 'off')}")
    if off is None:
        return None
    try:
        return int(off.get("y", "0")), int(off.get("x", "0"))
    except ValueError:
        return None


def _cell_text(tc):
    tx = tc.find(_q(A, "txBody"))
    if tx is None:
        return ""
    paras = [_paragraph_text(p) for p in tx.findall(_q(A, "p"))]
    text = "<br>".join(p.replace("\n", "<br>") for p in paras if p)
    return text.replace("|", "\\|")


def _table_markdown(tbl):
    rows = []
    for tr in tbl.findall(_q(A, "tr")):
        row = []
        for tc in tr.findall(_q(A, "tc")):
            # Merged-away cells repeat nothing; leave them blank.
            if tc.get("hMerge") == "1" or tc.get("vMerge") == "1":
                row.append("")
            else:
                row.append(_cell_text(tc))
        rows.append(row)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    if width == 0:
        return ""
    rows = [r + [""] * (width - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)


class _Slide:
    def __init__(self, number, rels):
        self.number = number
        self.rels = rels
        self.title = None
        self.blocks = []  # (offset or None, is_top_level, lines-as-string, single_line)
        self.images = []

    def image(self, blip):
        rid = blip.get(_q(R, "embed")) or blip.get(_q(R, "link"))
        rel = self.rels.get(rid)
        name = posixpath.basename(rel[1]) if rel else "image"
        self.images.append(f"[IMAGE: {name} — image on slide {self.number}]")


def _walk(slide, container, top_level):
    for el in container:
        tag = el.tag
        if tag == _q(MC, "AlternateContent"):
            # Fallback is plain DrawingML every reader understands; Choice may
            # use extension namespaces. Taking one avoids duplicate text.
            branch = el.find(_q(MC, "Fallback"))
            if branch is None:
                branch = el.find(_q(MC, "Choice"))
            if branch is not None:
                _walk(slide, branch, top_level)
        elif tag == _q(P, "sp"):
            ph = _placeholder_type(el)
            if ph in SKIP_PLACEHOLDERS:
                continue
            for blip in el.iter(_q(A, "blip")):
                slide.image(blip)
            if slide.title is None and ph in TITLE_PLACEHOLDERS:
                lines = _shape_lines(el, False)
                if lines:
                    slide.title = " ".join(" ".join(lines).split())
                    continue
            lines = _shape_lines(el, ph in BODY_PLACEHOLDERS)
            if lines:
                slide.blocks.append({
                    "offset": _offset(el) if top_level else None,
                    "fallback_ok": top_level and ph is None and len(lines) == 1
                    and "\n" not in lines[0] and not lines[0].startswith("- ")
                    and len(lines[0]) <= MAX_FALLBACK_TITLE,
                    "text": "\n".join(lines),
                })
        elif tag == _q(P, "grpSp"):
            _walk(slide, el, False)
        elif tag == _q(P, "graphicFrame"):
            tbl = el.find(f".//{_q(A, 'tbl')}")
            if tbl is not None:
                md = _table_markdown(tbl)
                if md:
                    slide.blocks.append({"offset": None, "fallback_ok": False, "text": md})
            elif el.find(f".//{_q(C, 'chart')}") is not None:
                slide.blocks.append({
                    "offset": None, "fallback_ok": False,
                    "text": f"[CHART — chart on slide {slide.number}]",
                })
        elif tag == _q(P, "pic"):
            for blip in el.iter(_q(A, "blip")):
                slide.image(blip)


def _fallback_title(slide):
    """Pick the strictly topmost single-line text box as the title, if any."""
    placed = [b for b in slide.blocks if b["offset"] is not None]
    if not placed:
        return
    top = min(placed, key=lambda b: b["offset"])
    if not top["fallback_ok"]:
        return
    # A tie on y means a row of labels, not a heading.
    if sum(1 for b in placed if b["offset"][0] == top["offset"][0]) > 1:
        return
    slide.title = top["text"]
    slide.blocks.remove(top)


def _notes_text(z, rels):
    for _rid, (rtype, target) in rels.items():
        if not rtype.endswith(REL_NOTES):
            continue
        try:
            root = _read_xml(z, target)
        except KeyError:
            return ""
        lines = []
        for sp in root.iter(_q(P, "sp")):
            if _placeholder_type(sp) == "body":
                lines.extend(_shape_lines(sp, False))
        return "\n".join(lines).strip()
    return ""


def extract_slide(z, part, slide_number):
    """Extract title, body text, tables, images and notes from one slide.

    Returns a formatted Markdown string for the slide.
    """
    root = _read_xml(z, part)
    rels = _rels(z, part)
    slide = _Slide(slide_number, rels)
    sp_tree = root.find(f"{_q(P, 'cSld')}/{_q(P, 'spTree')}")
    if sp_tree is not None:
        _walk(slide, sp_tree, True)
    if slide.title is None:
        _fallback_title(slide)

    header = f"## Slide {slide_number}"
    if slide.title:
        header += f" — {slide.title}"
    lines = [header]
    if root.get("show") == "0":
        lines += ["", "*(hidden slide)*"]
    for block in slide.blocks:
        lines += ["", block["text"]]
    if slide.images:
        lines.append("")
        lines.extend(slide.images)
    notes = _notes_text(z, rels)
    if notes:
        lines += ["", "### Notes", "", notes]
    return "\n".join(lines)


def pptx_to_markdown(pptx_path, out_dir, out_path=None):
    """Extract all slides from ``pptx_path`` into one .md file.

    Writes ``out_dir/<stem>.md``, or ``out_path`` when given. Returns the number
    of slides extracted.
    """
    stem = os.path.splitext(os.path.basename(pptx_path))[0]
    if out_path is None:
        out_path = os.path.join(out_dir, stem + ".md")

    with zipfile.ZipFile(pptx_path) as z:
        sections = []
        for i, part in enumerate(_slide_order(z)):
            try:
                sections.append(extract_slide(z, part, i + 1))
            except KeyError:
                # Listed in presentation.xml but missing from the package.
                sections.append(f"## Slide {i + 1}\n\n*(slide part missing: {part})*")

    content = f"# {stem}\n\n" + "\n\n".join(sections) + "\n"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)

    return len(sections)
