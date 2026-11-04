"""
assets.py — write a document's images into a folder beside its Markdown.

report.md gets report_files/, report.pdf.md gets report.pdf_files/. File names
are generated or sanitized here and never carry a path component from the
input, so nothing is written outside that folder. Identical images are stored
once.
"""

import hashlib
import os
import re
from urllib.parse import quote

ASSETS_SUFFIX = "_files"


def assets_dir_for(out_path):
    base = out_path[:-3] if out_path.endswith(".md") else out_path
    return base + ASSETS_SUFFIX


def safe_name(name, default="image"):
    """Reduce an archive member name to a harmless file name."""
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).lstrip("._")
    return name[:100] or default


def md_image(alt, link):
    alt = alt.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
    return f"![{alt}]({quote(link)})"


class AssetWriter:
    """Writes images for one Markdown file and remembers what it wrote."""

    def __init__(self, out_path):
        self.dir = assets_dir_for(out_path)
        self.dirname = os.path.basename(self.dir)
        self.written = []   # paths relative to the Markdown file's folder
        self._by_hash = {}
        self._names = set()

    def add(self, data, name):
        """Store ``data`` under (a sanitized) ``name``; return the link to it."""
        digest = hashlib.sha256(data).hexdigest()
        if digest in self._by_hash:
            return self._by_hash[digest]
        name = safe_name(name)
        stem, ext = os.path.splitext(name)
        candidate, n = name, 1
        while candidate.lower() in self._names:
            n += 1
            candidate = f"{stem}-{n}{ext}"
        self._names.add(candidate.lower())
        os.makedirs(self.dir, exist_ok=True)
        path = os.path.join(self.dir, candidate)
        # safe_name strips separators, so this holds by construction; keep the
        # check so a future change can't silently break it.
        if os.path.dirname(os.path.abspath(path)) != os.path.abspath(self.dir):
            raise ValueError(f"refusing to write outside {self.dir}: {candidate}")
        with open(path, "wb") as f:
            f.write(data)
        link = f"{self.dirname}/{candidate}"
        self._by_hash[digest] = link
        self.written.append(link)
        return link
