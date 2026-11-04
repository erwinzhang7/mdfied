"""
walk.py — find convertible files under a set of paths and decide where each
one's Markdown goes.

Directories are walked recursively. Version-control directories and the output
directory are never entered, hidden entries are skipped unless asked for, and
directory symlinks are not followed, so a symlink loop cannot hang the walk.
`.mdfyignore` files use gitignore syntax and apply to their own subtree, with
deeper files taking precedence, as in git. Patterns given on the command line
apply from each root and take precedence over every ignore file.
"""

import os
import posixpath
from dataclasses import dataclass, field

import pathspec

IGNORE_FILE = ".mdfyignore"
VCS_DIRS = {".git", ".hg", ".svn", ".bzr", ".jj", "_darcs", "CVS"}


@dataclass
class Source:
    path: str          # path to open, as reached from the root the user gave
    rel: str           # posix path relative to its root (basename for file args)
    ext: str           # lower-case extension, with the dot
    out_rel: str = ""  # posix path of the output, relative to the output dir


@dataclass
class Scan:
    sources: list = field(default_factory=list)
    ignored: list = field(default_factory=list)   # (display path, reason)
    missing: list = field(default_factory=list)
    unsupported: list = field(default_factory=list)


def _spec(lines):
    return pathspec.GitIgnoreSpec.from_lines(lines)


def _ignored_by(rules, rel, is_dir):
    """Return the source of the last matching rule if ``rel`` is ignored.

    ``rules`` is a list of (base_dir, spec, label), shallowest first. Each spec
    sees the path relative to its base, so a nested file's anchored patterns
    anchor at its own directory.
    """
    verdict, label = None, None
    for base, spec, src in rules:
        if base:
            if not rel.startswith(base + "/"):
                continue
            sub = rel[len(base) + 1:]
        else:
            sub = rel
        res = spec.check_file(sub + "/" if is_dir else sub)
        if res.include is not None:
            verdict, label = res.include, src
    return label if verdict else None


def _read_ignore_file(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read().splitlines()


def walk_dir(root, exts, scan, out_real=None, hidden=False,
             use_ignore_files=True, patterns=()):
    """Append every matching file under ``root`` to ``scan.sources``."""
    root_real = os.path.realpath(root)
    cli = [("", _spec(patterns), "--ignore")] if patterns else []

    def visit(dirpath, rel_dir, rules):
        if use_ignore_files:
            ign = os.path.join(dirpath, IGNORE_FILE)
            if os.path.isfile(ign):
                label = posixpath.join(rel_dir, IGNORE_FILE)
                rules = rules + [(rel_dir, _spec(_read_ignore_file(ign)), label)]
        try:
            with os.scandir(dirpath) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError as exc:
            scan.ignored.append((dirpath.rstrip("/") + "/", f"unreadable: {exc.strerror}"))
            return
        for e in entries:
            name = e.name
            if name == IGNORE_FILE:
                continue
            rel = posixpath.join(rel_dir, name) if rel_dir else name
            if name.startswith(".") and not hidden:
                continue
            if e.is_dir(follow_symlinks=False):
                if name in VCS_DIRS:
                    continue
                if out_real is not None and os.path.realpath(e.path) == out_real:
                    continue
                why = _ignored_by(rules + cli, rel, True)
                if why:
                    scan.ignored.append((os.path.join(root, rel) + "/", why))
                    continue
                visit(e.path, rel, rules)
            elif e.is_symlink() and os.path.isdir(e.path):
                continue  # directory symlinks are not followed
            elif e.is_file():
                ext = os.path.splitext(name)[1].lower()
                if ext not in exts:
                    continue
                why = _ignored_by(rules + cli, rel, False)
                if why:
                    scan.ignored.append((os.path.join(root, rel), why))
                    continue
                scan.sources.append(Source(os.path.join(root, rel), rel, ext))

    # An output dir equal to the root holds only .md files, so it needs no
    # exclusion; excluding it would skip the whole tree.
    if out_real == root_real:
        out_real = None
    visit(root, "", [])


def scan_paths(paths, exts, out_dir, hidden=False, use_ignore_files=True,
               patterns=()):
    """Expand file and directory arguments into a Scan with output names set.

    Output names mirror the tree under each directory root (a/b/x.pdf ->
    a/b/x.md); file arguments land at the top of the output dir. When two
    sources in one output folder share a stem (report.pdf, report.pptx), all of
    them keep their extension instead (report.pdf.md, report.pptx.md). Stems are
    compared case-insensitively, since macOS and Windows file systems are.
    """
    scan = Scan()
    out_real = os.path.realpath(out_dir)
    for p in paths:
        if os.path.isdir(p):
            walk_dir(p, exts, scan, out_real, hidden, use_ignore_files, patterns)
        elif os.path.isfile(p):
            ext = os.path.splitext(p)[1].lower()
            if ext in exts:
                scan.sources.append(Source(p, os.path.basename(p), ext))
            else:
                scan.unsupported.append(p)
        else:
            scan.missing.append(p)
    # The same file reached twice (`mdfy . a/x.pdf`) is converted once; the
    # first way it was reached decides its output path.
    seen, unique = set(), []
    for s in scan.sources:
        key = os.path.realpath(s.path)
        if key not in seen:
            seen.add(key)
            unique.append(s)
    scan.sources = unique
    _assign_outputs(scan.sources)
    return scan


def _assign_outputs(sources):
    groups = {}
    for s in sources:
        d, name = posixpath.split(s.rel)
        stem = os.path.splitext(name)[0]
        groups.setdefault((d.casefold(), stem.casefold()), []).append(s)
    for members in groups.values():
        for s in members:
            d, name = posixpath.split(s.rel)
            out_name = (name if len(members) > 1 else os.path.splitext(name)[0]) + ".md"
            s.out_rel = posixpath.join(d, out_name) if d else out_name
