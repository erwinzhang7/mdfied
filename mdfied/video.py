"""
video.py — Transcribe video/audio files to chunked Markdown.

Uses faster-whisper (CTranslate2) with int8 compute — several times faster than
openai-whisper on CPU at the same model accuracy — and transcribes multiple
files concurrently (CTranslate2 releases the GIL during inference), saturating
the available cores.

Whisper segments are grouped into overlapping word-based chunks. Each chunk
becomes a `## [HH:MM:SS]` section in a .md file: talk.mp4 -> talk.md.

Works on any media file faster-whisper/ffmpeg can decode (mp4, mkv, mov, webm,
mp3, wav, m4a, ...) — there is no filename convention.
"""

import os
import threading
import concurrent.futures

from tqdm import tqdm

DEFAULT_MODEL = "base"
DEFAULT_WORKERS = 4   # files transcribed in parallel
DEFAULT_THREADS = 4   # CTranslate2 cpu_threads per worker
DEFAULT_CHUNK_SIZE = 200    # target words per chunk
DEFAULT_CHUNK_OVERLAP = 30  # word overlap between adjacent chunks

# One model instance per worker thread (a model is not meant to be shared across
# concurrent transcribe calls). threading.local gives each pool worker its own
# model, created lazily on first use.
_local = threading.local()


def _get_model(model_size, cpu_threads):
    key = (model_size, cpu_threads)
    if getattr(_local, "key", None) != key:
        # Imported here so the CLI, PDF and PPTX paths don't pay for loading
        # CTranslate2 (or need it installed).
        from faster_whisper import WhisperModel

        _local.model = WhisperModel(
            model_size, device="cpu", compute_type="int8", cpu_threads=cpu_threads
        )
        _local.key = key
    return _local.model


def fmt_timestamp(seconds):
    """Convert float seconds to an HH:MM:SS string."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def chunk_segments(segments, chunk_size, chunk_overlap):
    """Group transcription segments into overlapping word-based chunks.

    Each segment is a dict ``{"start": float, "text": str}``. Returns a list of
    ``{"timestamp": "HH:MM:SS", "text": str}`` dicts. Words accumulate until the
    flush threshold (chunk_size - chunk_overlap) is reached; the last
    ``chunk_overlap`` words carry over into the next chunk for continuity.
    """
    flush_at = chunk_size - chunk_overlap
    chunks = []
    current_words = []
    current_start = None  # start time (seconds) of the first segment in this chunk

    for seg in segments:
        seg_words = seg["text"].split()
        if not seg_words:
            continue
        if current_start is None:
            current_start = seg["start"]
        current_words.extend(seg_words)

        while len(current_words) >= flush_at:
            chunk_text = " ".join(current_words[:flush_at + chunk_overlap])
            chunks.append({"timestamp": fmt_timestamp(current_start), "text": chunk_text})
            current_words = current_words[flush_at:]
            # Carried-over words lack a reliable timestamp; reset and let the next
            # consumed segment set the start time.
            current_start = None

    if current_words:
        chunks.append({
            "timestamp": fmt_timestamp(current_start if current_start is not None else 0.0),
            "text": " ".join(current_words),
        })

    return chunks


def build_markdown(title, chunks):
    """Render chunk dicts into a Markdown string with an H1 title."""
    lines = [f"# {title}", ""]
    for chunk in chunks:
        lines.append(f"## [{chunk['timestamp']}]")
        lines.append("")
        lines.append(chunk["text"])
        lines.append("")
    return "\n".join(lines)


def video_to_markdown(
    video_path,
    out_dir,
    model_size=DEFAULT_MODEL,
    cpu_threads=DEFAULT_THREADS,
    chunk_size=DEFAULT_CHUNK_SIZE,
    chunk_overlap=DEFAULT_CHUNK_OVERLAP,
    out_path=None,
):
    """Transcribe one media file and write its .md. Returns (stem, n_chunks).

    Writes ``out_dir/<stem>.md``, or ``out_path`` when given.
    """
    model = _get_model(model_size, cpu_threads)
    segments, _info = model.transcribe(video_path)
    seg_dicts = [{"start": s.start, "text": s.text} for s in segments]

    chunks = chunk_segments(seg_dicts, chunk_size, chunk_overlap)
    stem = os.path.splitext(os.path.basename(video_path))[0]
    md_content = build_markdown(stem, chunks)

    if out_path is None:
        out_path = os.path.join(out_dir, stem + ".md")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    return stem, len(chunks)


def transcribe_all(
    video_paths,
    out_dir,
    model_size=DEFAULT_MODEL,
    num_workers=DEFAULT_WORKERS,
    cpu_threads=DEFAULT_THREADS,
    chunk_size=DEFAULT_CHUNK_SIZE,
    chunk_overlap=DEFAULT_CHUNK_OVERLAP,
    out_paths=None,
):
    """Transcribe many files concurrently.

    ``out_paths``, if given, is a list parallel to ``video_paths`` naming each
    output file. Returns (total_chunks, total_files, failed) where ``failed`` is
    a list of (path, exception).
    """
    if out_paths is None:
        out_paths = [None] * len(video_paths)
    print(
        f"Transcribing {len(video_paths)} file(s) with faster-whisper '{model_size}' "
        f"(int8, {num_workers} workers x {cpu_threads} threads)..."
    )
    total_chunks = 0
    total_files = 0
    failed = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as pool:
        futures = {
            pool.submit(
                video_to_markdown, v, out_dir, model_size, cpu_threads,
                chunk_size, chunk_overlap, o,
            ): v
            for v, o in zip(video_paths, out_paths)
        }
        for fut in tqdm(
            concurrent.futures.as_completed(futures),
            total=len(futures), desc="Transcribing", unit="file",
        ):
            path = futures[fut]
            try:
                stem, n_chunks = fut.result()
            except Exception as exc:
                tqdm.write(f"  Warning: failed to transcribe '{path}': {exc}")
                failed.append((path, exc))
                continue
            tqdm.write(f"    -> {stem}: {n_chunks} chunks")
            total_chunks += n_chunks
            total_files += 1
    return total_chunks, total_files, failed
