"""
cli.py — command-line entry point for mdfied.

    mdfied pdf   <path> [path ...] [-o OUT]
    mdfied video <path> [path ...] [-o OUT] [--model base] [--workers 4] ...

Each path may be a file or a directory; directories are scanned for matching
extensions. Output Markdown is written to OUT (default: ./md).
"""

import argparse
import os
import sys

from . import video as video_mod
from .pdf import pdf_to_markdown

PDF_EXTS = {".pdf"}
# Containers/audio that ffmpeg (via faster-whisper) can decode.
VIDEO_EXTS = {
    ".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v",
    ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac",
}


def gather_files(paths, exts):
    """Expand a list of file/dir paths into a sorted list of matching files."""
    files = []
    for p in paths:
        if os.path.isdir(p):
            for name in sorted(os.listdir(p)):
                if os.path.splitext(name)[1].lower() in exts:
                    files.append(os.path.join(p, name))
        elif os.path.isfile(p):
            if os.path.splitext(p)[1].lower() in exts:
                files.append(p)
            else:
                print(f"Skipping '{p}': unsupported extension", file=sys.stderr)
        else:
            print(f"Skipping '{p}': not found", file=sys.stderr)
    return files


def cmd_pdf(args):
    files = gather_files(args.paths, PDF_EXTS)
    if not files:
        print("No PDF files found.", file=sys.stderr)
        return 1

    from tqdm import tqdm

    total_pages = 0
    for pdf_path in tqdm(files, desc="Extracting PDFs", unit="file"):
        try:
            total_pages += pdf_to_markdown(pdf_path, args.output)
        except Exception as exc:
            tqdm.write(f"  Warning: failed on '{pdf_path}': {exc}")

    print(f"\nExtracted {total_pages} pages from {len(files)} PDF(s)")
    print(f"Output written to: {os.path.abspath(args.output)}")
    return 0


def cmd_video(args):
    files = gather_files(args.paths, VIDEO_EXTS)
    if not files:
        print("No video/audio files found.", file=sys.stderr)
        return 1

    total_chunks, total_files = video_mod.transcribe_all(
        files,
        args.output,
        model_size=args.model,
        num_workers=args.workers,
        cpu_threads=args.threads,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
    )
    print(f"\nExtracted {total_chunks} chunks from {total_files} file(s)")
    print(f"Output written to: {os.path.abspath(args.output)}")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="mdfied",
        description="Turn PDFs and videos into clean, chunked Markdown.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_pdf = sub.add_parser("pdf", help="extract PDF(s) to Markdown")
    p_pdf.add_argument("paths", nargs="+", help="PDF file(s) or folder(s)")
    p_pdf.add_argument("-o", "--output", default="./md", help="output dir (default: ./md)")
    p_pdf.set_defaults(func=cmd_pdf)

    p_vid = sub.add_parser("video", help="transcribe video/audio to Markdown")
    p_vid.add_argument("paths", nargs="+", help="media file(s) or folder(s)")
    p_vid.add_argument("-o", "--output", default="./md", help="output dir (default: ./md)")
    p_vid.add_argument("--model", default=video_mod.DEFAULT_MODEL,
                       help="whisper model size: tiny, base, small, medium, large "
                            f"(default: {video_mod.DEFAULT_MODEL})")
    p_vid.add_argument("--workers", type=int, default=video_mod.DEFAULT_WORKERS,
                       help=f"files transcribed in parallel (default: {video_mod.DEFAULT_WORKERS})")
    p_vid.add_argument("--threads", type=int, default=video_mod.DEFAULT_THREADS,
                       help=f"CPU threads per worker (default: {video_mod.DEFAULT_THREADS})")
    p_vid.add_argument("--chunk-size", type=int, default=video_mod.DEFAULT_CHUNK_SIZE,
                       help=f"target words per chunk (default: {video_mod.DEFAULT_CHUNK_SIZE})")
    p_vid.add_argument("--chunk-overlap", type=int, default=video_mod.DEFAULT_CHUNK_OVERLAP,
                       help=f"word overlap between chunks (default: {video_mod.DEFAULT_CHUNK_OVERLAP})")
    p_vid.set_defaults(func=cmd_video)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
