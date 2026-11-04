"""
pdf.py — Extract per-page text and image references from PDF files into Markdown.

For each PDF, PyMuPDF (fitz) walks every page, identifies the page title (the
first block with a font size > 14pt, falling back to the largest font on the
page), collects the body text, and saves the embedded images to report_files/
beside the Markdown (or only notes them, with images=False). One Markdown file
is written per PDF: report.pdf -> report.md.

Images are written once per PDF object (and once per identical content), tiny
decorative images are skipped unless all_images=True, and anything that isn't
a plain RGB or grey PNG/JPEG (CMYK, soft masks, JPEG 2000, JBIG2, ...) is
re-encoded as PNG so it displays everywhere.

Works on any PDF — there is no filename convention. Page sections are emitted as
`## Page N — Title`.
"""

import os

from .assets import AssetWriter, md_image

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24.3; the old `fitz` name now warns
except ImportError:
    import fitz


# Font size (pt) above which a text block is treated as a candidate page title.
TITLE_FONT_THRESHOLD = 14.0

# Images smaller than this many pixels, or thinner than MIN_IMAGE_SIDE, are
# rules, bullets and logos; skipped unless all_images is set.
MIN_IMAGE_PIXELS = 48 * 48
MIN_IMAGE_SIDE = 8
# Decoding larger images to re-encode them could take gigabytes of memory.
MAX_DECODE_PIXELS = 40_000_000


def _reference_lines(page, page_number):
    """The text-only form: one note per image, nothing written."""
    return [
        f"[IMAGE: img_{img[0]}.png — image on page {page_number}]"
        for img in page.get_images(full=True)
    ]


def _image_bytes(doc, xref, smask):
    """Return (bytes, ext) for an image, re-encoding to PNG where needed."""
    info = doc.extract_image(xref)
    if not info:
        raise ValueError("no image data")
    ext = info.get("ext", "").lower()
    if not smask and ext in ("png", "jpeg", "jpg") and info.get("colorspace") in (1, 3):
        return info["image"], "jpg" if ext == "jpeg" else ext
    if info.get("width", 0) * info.get("height", 0) > MAX_DECODE_PIXELS:
        raise ValueError("too large to re-encode")
    pix = fitz.Pixmap(doc, xref)
    if pix.colorspace is not None and pix.colorspace.n not in (1, 3):
        pix = fitz.Pixmap(fitz.csRGB, pix)
    if smask:
        try:
            pix = fitz.Pixmap(pix, fitz.Pixmap(doc, smask))
        except Exception:
            pass  # a mask that doesn't fit is dropped rather than failing the image
    return pix.tobytes("png"), "png"


class _PdfImages:
    def __init__(self, doc, writer, all_images):
        self.doc = doc
        self.writer = writer
        self.all_images = all_images
        self.links = {}  # xref -> link, or None when skipped

    def __call__(self, page, page_number):
        lines = []
        for img in page.get_images(full=True):
            xref, smask, width, height = img[0], img[1], img[2], img[3]
            if xref not in self.links:
                self.links[xref] = self._save(xref, smask, width, height, page_number)
            link = self.links[xref]
            if link is None:
                continue
            if link.startswith("["):
                lines.append(link)
            else:
                lines.append(md_image(f"image on page {page_number}", link))
        return lines

    def _save(self, xref, smask, width, height, page_number):
        if not self.all_images and (
            width * height < MIN_IMAGE_PIXELS or min(width, height) < MIN_IMAGE_SIDE
        ):
            return None
        try:
            data, ext = _image_bytes(self.doc, xref, smask)
        except Exception as exc:
            return f"[IMAGE: not extracted ({exc}) — image on page {page_number}]"
        n = len(self.writer.written) + 1
        return self.writer.add(data, f"p{page_number}-{n}.{ext}")


def extract_page(page, page_number, images=None):
    """Extract title, body text, and image references from a single PDF page.

    Returns a formatted Markdown string for the page.
    """
    # Plain text blocks as a fallback. Each item is
    # (x0, y0, x1, y1, text, block_no, block_type); block_type 0 = text.
    blocks = page.get_text("blocks")
    text_blocks = [b[4].strip() for b in blocks if b[6] == 0 and b[4].strip()]

    title_text = None
    body_parts = []

    try:
        detail = page.get_text("dict")
        # (max_font_size_on_block, full_text) for every text block.
        block_info = []
        for blk in detail.get("blocks", []):
            if blk.get("type") != 0:
                continue
            block_text_parts = []
            max_size = 0.0
            for line in blk.get("lines", []):
                for span in line.get("spans", []):
                    max_size = max(max_size, span.get("size", 0))
                    t = span.get("text", "").strip()
                    if t:
                        block_text_parts.append(t)
            full_text = " ".join(block_text_parts).strip()
            if full_text:
                block_info.append((max_size, full_text))

        if block_info:
            # Title: first block above the font threshold; else the largest font.
            title_idx = next(
                (i for i, (sz, _) in enumerate(block_info) if sz > TITLE_FONT_THRESHOLD),
                None,
            )
            if title_idx is None:
                title_idx = max(range(len(block_info)), key=lambda i: block_info[i][0])

            title_text = block_info[title_idx][1]
            body_parts = [txt for i, (_, txt) in enumerate(block_info) if i != title_idx]
    except Exception:
        # If detailed parsing fails, fall back to plain text blocks.
        if text_blocks:
            title_text = text_blocks[0]
            body_parts = text_blocks[1:]

    image_refs = (images or _reference_lines)(page, page_number)

    # Assemble Markdown.
    lines = []
    if title_text:
        lines.append(f"## Page {page_number} — {title_text}")
    else:
        lines.append(f"## Page {page_number}")

    body = "\n".join(body_parts).strip()
    if body:
        lines.append("")
        lines.append(body)

    if image_refs:
        lines.append("")
        lines.extend(image_refs)

    return "\n".join(lines)


def convert_pdf(pdf_path, out_path, images=True, all_images=False):
    """Write ``out_path`` (and its image folder). Returns (pages, asset links)."""
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    writer = AssetWriter(out_path)

    doc = fitz.open(pdf_path)
    sections = []
    try:
        handler = _PdfImages(doc, writer, all_images) if images else None
        for page_index in range(len(doc)):
            page = doc.load_page(page_index)
            sections.append(extract_page(page, page_index + 1, handler))
    finally:
        doc.close()

    content = f"# {stem}\n\n" + "\n\n".join(sections) + "\n"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)

    return len(sections), writer.written


def pdf_to_markdown(pdf_path, out_dir, out_path=None, images=True, all_images=False):
    """Extract all pages from ``pdf_path`` into one .md file.

    Writes ``out_dir/<stem>.md``, or ``out_path`` when given, with images in
    ``<stem>_files/`` beside it (``images=False`` only notes them). Returns the
    number of pages extracted.
    """
    if out_path is None:
        stem = os.path.splitext(os.path.basename(pdf_path))[0]
        out_path = os.path.join(out_dir, stem + ".md")
    return convert_pdf(pdf_path, out_path, images, all_images)[0]
