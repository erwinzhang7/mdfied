"""
cli.py — command-line entry point (`mdfy`, also installed as `mdfied`).

    mdfy [PATH ...] [options]          every supported file, by extension
    mdfy pdf   [PATH ...] [options]    PDFs only
    mdfy pptx  [PATH ...] [options]    PowerPoint decks only
    mdfy video [PATH ...] [options]    video/audio only

PATH defaults to the current directory. Directories are walked recursively and
the output mirrors the source tree under -o (default: ./md), with each
document's images in a <name>_files/ folder beside its Markdown. What was
written is recorded in <out>/.mdfy-manifest.json: unchanged sources are skipped
unless --force is given, and only files listed there are ever deleted.
"""

import argparse
import os
import posixpath
import sys

from . import __version__
from . import video as video_mod
from .manifest import Manifest
from .walk import IGNORE_FILE, scan_paths

PROG = "mdfy"

PDF_EXTS = {".pdf"}
# .pptm/.ppsx/.potx share the .pptx layout; macros in .pptm are never read.
PPTX_EXTS = {".pptx", ".pptm", ".ppsx", ".potx"}
# Containers/audio that ffmpeg (via faster-whisper) can decode.
VIDEO_EXTS = {
    ".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v",
    ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac",
}
KINDS = {"pdf": PDF_EXTS, "pptx": PPTX_EXTS, "video": VIDEO_EXTS}
ALL_EXTS = PDF_EXTS | PPTX_EXTS | VIDEO_EXTS
UNITS = {"pdf": "pages", "pptx": "slides", "video": "chunks"}


def kind_of(ext):
    for kind, exts in KINDS.items():
        if ext in exts:
            return kind
    return None


def opts_key(kind, args):
    """What, besides the source, decides an output's content."""
    if kind == "video":
        return ""
    if not args.images:
        return "noimg"
    return "img+all" if (kind == "pdf" and args.all_images) else "img"


class Plan:
    def __init__(self):
        self.todo = []      # (kind, source, out_rel)
        self.fresh = []
        self.clashes = []   # (source, out_rel, other source)
        self.renamed = []   # (stale out_rel, new out_rel)
        self.prune = []     # (out_rel, reason)


def is_fresh(manifest, kind, src, rel, args):
    if manifest.get(rel) is not None:
        return manifest.up_to_date(rel, src, opts_key(kind, args))
    # No record: written by mdfy before 0.3, or by hand. Documents are cheap to
    # redo; a transcript counts as current when it is newer than the media.
    if kind != "video":
        return False
    try:
        return os.path.getmtime(manifest.abs(rel)) >= os.path.getmtime(src)
    except OSError:
        return False


def make_plan(args, kinds, manifest):
    """Scan the paths and decide what to convert, skip, rename and prune.

    Output names are decided over every supported type, even when a subcommand
    converts only one, so a file's output path doesn't depend on the command.
    """
    paths = args.paths or ["."]
    scan = scan_paths(
        paths, ALL_EXTS, args.output,
        hidden=args.hidden, use_ignore_files=not args.no_ignore,
        patterns=args.ignore,
    )
    plan = Plan()
    explicit = {os.path.abspath(p) for p in paths if os.path.isfile(p)}
    taken = {s.out_rel.casefold() for s in scan.sources}
    claimed = {}
    for s in scan.sources:
        kind = kind_of(s.ext)
        if kind not in kinds:
            # A file named on the command line but of another type.
            if os.path.abspath(s.path) in explicit:
                scan.unsupported.append(s.path)
            continue
        key = s.out_rel.casefold()
        if key in claimed:
            plan.clashes.append((s.path, s.out_rel, claimed[key]))
            continue
        claimed[key] = s.path
        if not args.force and is_fresh(manifest, kind, s.path, s.out_rel, args):
            plan.fresh.append((kind, s.path, s.out_rel))
        else:
            plan.todo.append((kind, s.path, s.out_rel))
        # A collision appearing or going away renames the output
        # (report.md <-> report.pdf.md); the old name is stale.
        d = posixpath.dirname(s.out_rel)
        name = posixpath.basename(s.rel)
        stem = os.path.splitext(name)[0]
        real = os.path.realpath(s.path)
        for old_name in {stem + ".md", name + ".md"}:
            old = posixpath.join(d, old_name) if d else old_name
            e = manifest.get(old)
            if old != s.out_rel and old.casefold() not in taken and e \
                    and e.get("source") == real:
                plan.renamed.append((old, s.out_rel))

    if args.prune:
        live = {os.path.realpath(s.path) for s in scan.sources}
        roots = [os.path.realpath(p) for p in paths if os.path.isdir(p)]
        for rel, e in sorted(manifest.outputs.items()):
            src = e.get("source", "")
            if e.get("kind") not in kinds or rel.casefold() in taken:
                continue
            in_scope = src in {os.path.realpath(p) for p in explicit} or any(
                src == r or src.startswith(r.rstrip(os.sep) + os.sep) for r in roots
            )
            if not in_scope:
                continue
            if not os.path.exists(src):
                plan.prune.append((rel, "source deleted"))
            elif src not in live:
                plan.prune.append((rel, "source ignored"))

    # Report ignored files only for the types this command converts.
    scan.ignored = [
        (path, why) for path, why in scan.ignored
        if path.endswith("/") or kind_of(os.path.splitext(path)[1].lower()) in kinds
    ]
    return scan, plan


def convert_doc(kind, src, out, args):
    if kind == "pdf":
        from .pdf import convert_pdf
        return convert_pdf(src, out, images=args.images, all_images=args.all_images)
    from .pptx import convert_pptx
    return convert_pptx(src, out, images=args.images)


def record(manifest, kind, src, rel, st, args, links):
    d = posixpath.dirname(rel)
    assets = [posixpath.join(d, link) if d else link for link in links]
    old = manifest.get(rel)
    if old:
        manifest.remove_assets(rel, keep=set(assets))
    manifest.record(rel, kind, src, st, opts_key(kind, args), assets)


def delete_owned(manifest, rel, why):
    """Remove an output mdfy wrote, unless someone has changed it since."""
    if manifest.owned_unchanged(rel):
        manifest.remove(rel)
        print(f"Removed {manifest.abs(rel)} ({why})")
    else:
        if os.path.exists(manifest.abs(rel)):
            print(f"Left {manifest.abs(rel)} in place ({why}, but it was "
                  "changed after mdfy wrote it)", file=sys.stderr)
        manifest.forget(rel)


def run(args, kinds):
    manifest = Manifest(args.output)
    scan, plan = make_plan(args, kinds, manifest)
    out = manifest.abs

    for p in scan.missing:
        print(f"Skipping '{p}': not found", file=sys.stderr)
    for p in scan.unsupported:
        print(f"Skipping '{p}': unsupported extension", file=sys.stderr)
    for src, rel, other in plan.clashes:
        print(f"Skipping '{src}': output '{out(rel)}' is already taken by '{other}'",
              file=sys.stderr)

    if args.dry_run:
        for _kind, src, rel in plan.todo:
            print(f"convert  {src} -> {out(rel)}")
        for _kind, src, rel in plan.fresh:
            print(f"skip     {src} (up to date: {out(rel)})")
        for old, new in plan.renamed:
            print(f"remove   {out(old)} (renamed to {posixpath.basename(new)})")
        for rel, why in plan.prune:
            print(f"prune    {out(rel)} ({why})")
        for path, why in scan.ignored:
            print(f"ignore   {path} ({why})")
        extra = f", {len(plan.prune)} to prune" if args.prune else ""
        print(f"\n{len(plan.todo)} to convert, {len(plan.fresh)} up to date, "
              f"{len(scan.ignored)} ignored{extra}")
        return 1 if (scan.missing or plan.clashes) else 0

    if not plan.todo and not plan.fresh and not plan.prune:
        print("No supported files found.", file=sys.stderr)
        return 1

    stats = {k: {"done": 0, "units": 0, "fresh": 0, "failed": 0}
             for k in KINDS if k in kinds}
    for kind, _src, _rel in plan.fresh:
        stats[kind]["fresh"] += 1
    for src, _rel, _other in plan.clashes:
        stats[kind_of(os.path.splitext(src)[1].lower())]["failed"] += 1
    ok = {rel for _k, _s, rel in plan.fresh}

    try:
        docs = [t for t in plan.todo if t[0] != "video"]
        if docs:
            from tqdm import tqdm
            for kind, src, rel in tqdm(docs, desc="Extracting", unit="file"):
                try:
                    st = os.stat(src)
                    n, links = convert_doc(kind, src, out(rel), args)
                except Exception as exc:
                    tqdm.write(f"  Warning: failed on '{src}': {exc}")
                    stats[kind]["failed"] += 1
                    continue
                record(manifest, kind, src, rel, st, args, links)
                ok.add(rel)
                stats[kind]["done"] += 1
                stats[kind]["units"] += n

        videos = [t for t in plan.todo if t[0] == "video"]
        if videos:
            stat_before = {src: os.stat(src) for _k, src, _r in videos}
            total_chunks, total_files, failed = video_mod.transcribe_all(
                [src for _k, src, _r in videos],
                args.output,
                model_size=args.model,
                num_workers=args.workers,
                cpu_threads=args.threads,
                chunk_size=args.chunk_size,
                chunk_overlap=args.chunk_overlap,
                out_paths=[out(rel) for _k, _s, rel in videos],
            )
            bad = {p for p, _exc in failed}
            for _k, src, rel in videos:
                if src not in bad:
                    record(manifest, "video", src, rel, stat_before[src], args, [])
                    ok.add(rel)
            stats["video"]["done"] += total_files
            stats["video"]["units"] += total_chunks
            stats["video"]["failed"] += len(failed)

        for old, new in plan.renamed:
            if new in ok:
                delete_owned(manifest, old, f"renamed to {posixpath.basename(new)}")
        for rel, why in plan.prune:
            delete_owned(manifest, rel, why)
    finally:
        manifest.save()

    print()
    for kind, st in stats.items():
        if not any(st.values()):
            continue
        print(f"{kind:6} {st['done']} converted ({st['units']} {UNITS[kind]}), "
              f"{st['fresh']} up to date, {st['failed']} failed")
    if plan.fresh:
        print(f"Skipped {len(plan.fresh)} up-to-date file(s); use --force to redo them.")
    if scan.ignored:
        print(f"Ignored {len(scan.ignored)} path(s).")
    print(f"Output: {os.path.abspath(args.output)}")

    failed = sum(st["failed"] for st in stats.values())
    return 1 if (failed or scan.missing) else 0


def _common(parser, video_opts, docs_opts=True):
    parser.add_argument("paths", nargs="*", metavar="PATH",
                        help="files or folders (default: current directory)")
    parser.add_argument("-o", "--output", default="./md",
                        help="output dir (default: ./md)")
    parser.add_argument("-f", "--force", action="store_true",
                        help="reconvert files whose Markdown is already up to date")
    parser.add_argument("-n", "--dry-run", action="store_true",
                        help="list what would be converted, skipped and ignored")
    parser.add_argument("--hidden", action="store_true",
                        help="include hidden files and folders")
    parser.add_argument("--ignore", action="append", default=[], metavar="PATTERN",
                        help="gitignore-style pattern to skip (repeatable)")
    parser.add_argument("--no-ignore", action="store_true",
                        help=f"don't read {IGNORE_FILE} files")
    parser.add_argument("--prune", action="store_true",
                        help="delete outputs mdfy wrote whose sources were deleted or "
                             "are now ignored")
    if docs_opts:
        parser.add_argument("--no-images", dest="images", action="store_false",
                            help="note images in the text instead of saving them")
        parser.add_argument("--all-images", action="store_true",
                            help="keep tiny PDF images (rules, bullets) too")
    else:
        parser.set_defaults(images=True, all_images=False)
    parser.add_argument("--version", action="version",
                        version=f"{PROG} {__version__}")
    if video_opts:
        g = parser.add_argument_group("video/audio options")
        g.add_argument("--model", default=video_mod.DEFAULT_MODEL,
                       help="whisper model size: tiny, base, small, medium, large "
                            f"(default: {video_mod.DEFAULT_MODEL})")
        g.add_argument("--workers", type=int, default=video_mod.DEFAULT_WORKERS,
                       help=f"files transcribed in parallel (default: {video_mod.DEFAULT_WORKERS})")
        g.add_argument("--threads", type=int, default=video_mod.DEFAULT_THREADS,
                       help=f"CPU threads per worker (default: {video_mod.DEFAULT_THREADS})")
        g.add_argument("--chunk-size", type=int, default=video_mod.DEFAULT_CHUNK_SIZE,
                       help=f"target words per chunk (default: {video_mod.DEFAULT_CHUNK_SIZE})")
        g.add_argument("--chunk-overlap", type=int, default=video_mod.DEFAULT_CHUNK_OVERLAP,
                       help=f"word overlap between chunks (default: {video_mod.DEFAULT_CHUNK_OVERLAP})")
    else:
        parser.set_defaults(model=None, workers=None, threads=None,
                            chunk_size=None, chunk_overlap=None)
    return parser


def build_parser(command=None):
    """Parser for the auto mode (command=None) or one subcommand."""
    if command is None:
        p = argparse.ArgumentParser(
            prog=PROG,
            usage=f"{PROG} [PATH ...] [options]\n       {PROG} {{pdf,pptx,video}} [PATH ...] [options]",
            description="Turn PDFs, PowerPoint decks, and video/audio into clean, chunked\n"
                        "Markdown. Folders are converted recursively, mirroring the tree\n"
                        "under the output dir. PATH defaults to the current directory.",
            epilog="subcommands:\n"
                   "  pdf     convert PDFs only\n"
                   "  pptx    convert PowerPoint decks only\n"
                   "  video   transcribe video/audio only\n\n"
                   f"Skip paths with a {IGNORE_FILE} file (gitignore syntax) in any "
                   "folder, or --ignore.",
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        return _common(p, video_opts=True)
    helps = {
        "pdf": "Extract PDFs to Markdown.",
        "pptx": "Extract PowerPoint decks to Markdown.",
        "video": "Transcribe video/audio to Markdown.",
    }
    p = argparse.ArgumentParser(prog=f"{PROG} {command}", description=helps[command])
    return _common(p, video_opts=command == "video", docs_opts=command != "video")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    command = argv[0] if argv and argv[0] in KINDS else None
    if command:
        argv = argv[1:]
    args = build_parser(command).parse_intermixed_args(argv)
    kinds = {command} if command else set(KINDS)
    return run(args, kinds)


if __name__ == "__main__":
    sys.exit(main())
