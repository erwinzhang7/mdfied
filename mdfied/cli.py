"""
cli.py — command-line entry point (`mdfy`, also installed as `mdfied`).

    mdfy [PATH ...] [options]          every supported file, by extension
    mdfy pdf   [PATH ...] [options]    PDFs only
    mdfy pptx  [PATH ...] [options]    PowerPoint decks only
    mdfy video [PATH ...] [options]    video/audio only

PATH defaults to the current directory. Directories are walked recursively and
the output mirrors the source tree under -o (default: ./md). Files whose
Markdown is already newer than the source are skipped unless --force is given.
"""

import argparse
import os
import sys

from . import __version__
from . import video as video_mod
from .walk import IGNORE_FILE, scan_paths

PROG = "mdfy"

PDF_EXTS = {".pdf"}
PPTX_EXTS = {".pptx"}
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


def up_to_date(src, out):
    try:
        return os.path.getmtime(out) >= os.path.getmtime(src)
    except OSError:
        return False


def plan(args, kinds):
    """Scan the paths and sort sources into convert / skip lists.

    Output names are decided over every supported type, even when a subcommand
    converts only one, so a file's output path doesn't depend on the command.
    """
    scan = scan_paths(
        args.paths or ["."], ALL_EXTS, args.output,
        hidden=args.hidden, use_ignore_files=not args.no_ignore,
        patterns=args.ignore,
    )
    explicit = {os.path.abspath(p) for p in args.paths if os.path.isfile(p)}
    todo, fresh, clashes = [], [], []
    claimed = {}
    for s in scan.sources:
        kind = kind_of(s.ext)
        if kind not in kinds:
            # A file named on the command line but of another type.
            if os.path.abspath(s.path) in explicit:
                scan.unsupported.append(s.path)
            continue
        out = os.path.join(args.output, *s.out_rel.split("/"))
        key = os.path.normcase(os.path.abspath(out)).casefold()
        if key in claimed:
            clashes.append((s, out, claimed[key]))
            continue
        claimed[key] = s.path
        item = (kind, s.path, out)
        if not args.force and up_to_date(s.path, out):
            fresh.append(item)
        else:
            todo.append(item)
    # Report ignored files only for the types this command converts.
    scan.ignored = [
        (path, why) for path, why in scan.ignored
        if path.endswith("/") or kind_of(os.path.splitext(path)[1].lower()) in kinds
    ]
    return scan, todo, fresh, clashes


def convert_one(kind, src, out):
    if kind == "pdf":
        from .pdf import pdf_to_markdown
        return pdf_to_markdown(src, None, out_path=out)
    from .pptx import pptx_to_markdown
    return pptx_to_markdown(src, None, out_path=out)


def run(args, kinds):
    scan, todo, fresh, clashes = plan(args, kinds)

    for p in scan.missing:
        print(f"Skipping '{p}': not found", file=sys.stderr)
    for p in scan.unsupported:
        print(f"Skipping '{p}': unsupported extension", file=sys.stderr)
    for s, out, other in clashes:
        print(f"Skipping '{s.path}': output '{out}' is already taken by '{other}'",
              file=sys.stderr)

    if args.dry_run:
        for kind, src, out in todo:
            print(f"convert  {src} -> {out}")
        for kind, src, out in fresh:
            print(f"skip     {src} (up to date: {out})")
        for path, why in scan.ignored:
            print(f"ignore   {path} ({why})")
        print(f"\n{len(todo)} to convert, {len(fresh)} up to date, "
              f"{len(scan.ignored)} ignored")
        return 1 if (scan.missing or clashes) else 0

    if not todo and not fresh:
        print("No supported files found.", file=sys.stderr)
        return 1

    stats = {k: {"done": 0, "units": 0, "fresh": 0, "failed": 0} for k in kinds}
    for kind, _src, _out in fresh:
        stats[kind]["fresh"] += 1
    for s, _out, _other in clashes:
        stats[kind_of(s.ext)]["failed"] += 1

    docs = [t for t in todo if t[0] != "video"]
    if docs:
        from tqdm import tqdm
        for kind, src, out in tqdm(docs, desc="Extracting", unit="file"):
            try:
                n = convert_one(kind, src, out)
            except Exception as exc:
                tqdm.write(f"  Warning: failed on '{src}': {exc}")
                stats[kind]["failed"] += 1
                continue
            stats[kind]["done"] += 1
            stats[kind]["units"] += n

    videos = [t for t in todo if t[0] == "video"]
    if videos:
        total_chunks, total_files, failed = video_mod.transcribe_all(
            [src for _k, src, _o in videos],
            args.output,
            model_size=args.model,
            num_workers=args.workers,
            cpu_threads=args.threads,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
            out_paths=[out for _k, _s, out in videos],
        )
        stats["video"]["done"] += total_files
        stats["video"]["units"] += total_chunks
        stats["video"]["failed"] += len(failed)

    print()
    for kind, st in stats.items():
        if not any(st.values()):
            continue
        print(f"{kind:6} {st['done']} converted ({st['units']} {UNITS[kind]}), "
              f"{st['fresh']} up to date, {st['failed']} failed")
    if fresh:
        print(f"Skipped {len(fresh)} up-to-date file(s); use --force to redo them.")
    if scan.ignored:
        print(f"Ignored {len(scan.ignored)} path(s).")
    print(f"Output: {os.path.abspath(args.output)}")

    failed = sum(st["failed"] for st in stats.values())
    return 1 if (failed or scan.missing) else 0


def _common(parser, video_opts):
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
    return _common(p, video_opts=command == "video")


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
