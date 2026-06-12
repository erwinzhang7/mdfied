# mdfied

Turn PDFs and videos into clean, chunked Markdown.

- **PDFs** → one `.md` per file, one `## Page N — Title` section per page, with image references.
- **Video / audio** → one `.md` per file, transcribed with [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (int8 CPU), split into overlapping word-based chunks tagged with `## [HH:MM:SS]` timestamps. Multiple files transcribe in parallel.

Works on any file — no naming convention required. Output filenames mirror the source (`report.pdf → report.md`, `talk.mp4 → talk.md`).

## Install

```bash
pip install -e .
# or just: pip install -r requirements.txt
```

`faster-whisper` needs **ffmpeg** available on your system (`brew install ffmpeg`).

## Usage

```bash
# PDFs — pass files and/or folders
mdfied pdf report.pdf
mdfied pdf ./papers/ -o ./out

# Video / audio
mdfied video talk.mp4
mdfied video ./lectures/ -o ./out --model small --workers 6

# Mixed folder? Each command picks up only its own file types.
mdfied pdf ./raw/ -o ./md
mdfied video ./raw/ -o ./md
```

Output defaults to `./md`.

### Video options

| Flag | Default | Description |
|---|---|---|
| `--model` | `base` | whisper size: `tiny`, `base`, `small`, `medium`, `large` |
| `--workers` | `4` | files transcribed in parallel |
| `--threads` | `4` | CPU threads per worker |
| `--chunk-size` | `200` | target words per chunk |
| `--chunk-overlap` | `30` | word overlap between adjacent chunks |

## Library use

```python
from mdfied import pdf_to_markdown, video_to_markdown

pdf_to_markdown("report.pdf", "out/")
video_to_markdown("talk.mp4", "out/", model_size="small")
```

## Supported inputs

- **PDF:** `.pdf`
- **Media:** `.mp4 .mkv .mov .webm .avi .m4v .mp3 .wav .m4a .flac .ogg .aac` (anything ffmpeg can decode)
