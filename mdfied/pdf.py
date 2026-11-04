"""
pdf.py — Extract per-page text and image references from PDF files into Markdown.

For each PDF, PyMuPDF (fitz) walks every page, identifies the page title (the
first block with a font size > 14pt, falling back to the largest font on the
page), collects the body text, and notes any embedded images. One Markdown file
is written per PDF: report.pdf -> report.md.

Works on any PDF — there is no filename convention. Page sections are emitted as
`## Page N — Title`.
"""

import os

try:
    import pymupdf as fitz  # PyMuPDF >= 1.24.3; the old `fitz` name now warns
except ImportError:
    import fitz


# Font size (pt) above which a text block is treated as a candidate page title.
TITLE_FONT_THRESHOLD = 14.0


def extract_page(page, page_number):
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

    # Image references.
    image_refs = [
        f"[IMAGE: img_{img[0]}.png — image on page {page_number}]"
        for img in page.get_images(full=True)
    ]

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


def pdf_to_markdown(pdf_path, out_dir, out_path=None):
    """Extract all pages from ``pdf_path`` into one .md file.

    Writes ``out_dir/<stem>.md``, or ``out_path`` when given. Returns the number
    of pages extracted.
    """
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    if out_path is None:
        out_path = os.path.join(out_dir, stem + ".md")

    doc = fitz.open(pdf_path)
    sections = []
    try:
        for page_index in range(len(doc)):
            page = doc.load_page(page_index)
            sections.append(extract_page(page, page_index + 1))
    finally:
        doc.close()

    content = f"# {stem}\n\n" + "\n\n".join(sections) + "\n"
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)

    return len(sections)
