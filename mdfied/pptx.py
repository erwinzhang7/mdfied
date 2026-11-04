"""
pptx.py — Extract per-slide text, tables, notes and images from PowerPoint
files (.pptx, .pptm, .ppsx, .potx) into Markdown.

For each .pptx, the slides are walked in presentation order (the slide-id list in
presentation.xml, resolved through its relationships, not filename order). The
slide title is the title/ctrTitle placeholder; decks without placeholders (Google
Slides exports, generated decks) fall back to the topmost single-line text box.
Every other text shape becomes body text in reading order, tables become
Markdown tables, pictures are copied to lecture_files/ beside the Markdown in
their original format (or only noted, with images=False), and speaker notes are
appended under `### Notes`. One Markdown file is written per deck:
lecture.pptx -> lecture.md.

Works on any .pptx — there is no filename convention. Slide sections are emitted
as `## Slide N — Title`, mirroring the PDF extractor's `## Page N — Title`.

A .pptx is a zip of XML parts. Parts are parsed with defusedxml (no entity
expansion, no external entities), each part's decompressed size is capped, and
only image parts referenced by a slide are ever copied out: each must have a
raster image extension, start with that format's signature, and fit under a
size cap, and it is written under a sanitized name inside the assets folder.
Macros (vbaProject.bin in .pptm) are never read.
"""

import os
import posixpath
import zipfile

from defusedxml import ElementTree as ET

from .assets import AssetWriter, md_image


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

# Media copied out of a deck: raster formats a Markdown viewer can show, keyed
# by extension, with the leading bytes each must start with. EMF/WMF and SVG
# (which can carry script) are noted but not copied.
MEDIA_SIGNATURES = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".bmp": (b"BM",),
    ".tif": (b"II*\x00", b"MM\x00*"),
    ".tiff": (b"II*\x00", b"MM\x00*"),
    ".webp": (b"RIFF",),
}
MAX_MEDIA_BYTES = 32 * 1024 * 1024

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


class _Media:
    """Copies image parts out of one deck, each part at most once."""

    def __init__(self, z, writer):
        self.z = z
        self.writer = writer
        self.links = {}  # part name -> link, or a text note when not copied

    def link(self, part):
        if part not in self.links:
            self.links[part] = self._copy(part)
        return self.links[part]

    def _copy(self, part):
        ext = posixpath.splitext(part)[1].lower()
        sigs = MEDIA_SIGNATURES.get(ext)
        if sigs is None:
            return "unsupported format"
        try:
            info = self.z.getinfo(part)
        except KeyError:
            return "missing from the file"
        if info.file_size > MAX_MEDIA_BYTES:
            return "too large"
        with self.z.open(info) as f:
            data = f.read(MAX_MEDIA_BYTES + 1)
        if len(data) > MAX_MEDIA_BYTES:
            return "too large"
        if not data.startswith(sigs) or (ext == ".webp" and data[8:12] != b"WEBP"):
            return "content does not match its extension"
        return self.writer.add(data, posixpath.basename(part))


class _Slide:
    def __init__(self, number, rels, media=None):
        self.number = number
        self.rels = rels
        self.media = media
        self.title = None
        self.blocks = []  # dicts: offset, fallback_ok, text
        self.images = []

    def image(self, blip, descr=None):
        rid = blip.get(_q(R, "embed")) or blip.get(_q(R, "link"))
        rel = self.rels.get(rid)
        name = posixpath.basename(rel[1]) if rel else "image"
        where = f"image on slide {self.number}"
        external = blip.get(_q(R, "embed")) is None
        if self.media is None or rel is None or external:
            self.images.append(f"[IMAGE: {name} — {where}]")
            return
        link = self.media.link(rel[1])
        if "/" in link:
            self.images.append(md_image(descr or where, link))
        else:
            self.images.append(f"[IMAGE: {name} not copied ({link}) — {where}]")


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
            c_nv = el.find(f"{_q(P, 'nvPicPr')}/{_q(P, 'cNvPr')}")
            descr = c_nv.get("descr") if c_nv is not None else None
            for blip in el.iter(_q(A, "blip")):
                slide.image(blip, (descr or "").strip() or None)


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


def extract_slide(z, part, slide_number, media=None):
    """Extract title, body text, tables, images and notes from one slide.

    Returns a formatted Markdown string for the slide.
    """
    root = _read_xml(z, part)
    rels = _rels(z, part)
    slide = _Slide(slide_number, rels, media)
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


def convert_pptx(pptx_path, out_path, images=True):
    """Write ``out_path`` (and its image folder). Returns (slides, asset links)."""
    stem = os.path.splitext(os.path.basename(pptx_path))[0]
    writer = AssetWriter(out_path)

    with zipfile.ZipFile(pptx_path) as z:
        media = _Media(z, writer) if images else None
        sections = []
        for i, part in enumerate(_slide_order(z)):
            try:
                sections.append(extract_slide(z, part, i + 1, media))
            except KeyError:
                # Listed in presentation.xml but missing from the package.
                sections.append(f"## Slide {i + 1}\n\n*(slide part missing: {part})*")

    content = f"# {stem}\n\n" + "\n\n".join(sections) + "\n"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)

    return len(sections), writer.written


def pptx_to_markdown(pptx_path, out_dir, out_path=None, images=True):
    """Extract all slides from ``pptx_path`` into one .md file.

    Writes ``out_dir/<stem>.md``, or ``out_path`` when given, with pictures in
    ``<stem>_files/`` beside it (``images=False`` only notes them). Returns the
    number of slides extracted.
    """
    if out_path is None:
        stem = os.path.splitext(os.path.basename(pptx_path))[0]
        out_path = os.path.join(out_dir, stem + ".md")
    return convert_pptx(pptx_path, out_path, images)[0]
