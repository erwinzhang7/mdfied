# mdfied

Turn PDFs, PowerPoint decks, and videos into clean, chunked Markdown. The command is `mdfy`.

- **PDFs** → one `.md` per file, one `## Page N — Title` section per page, with its images saved beside it.
- **PowerPoint** (`.pptx`, `.pptm`, `.ppsx`, `.potx`) → one `.md` per deck, one `## Slide N — Title` section per slide (in presentation order), with bullets, tables as Markdown tables, pictures saved beside it, and speaker notes. Reads the file directly; no PowerPoint or LibreOffice needed, and macros are never read.
- **Video / audio** → one `.md` per file, transcribed with [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (int8 CPU), split into overlapping word-based chunks tagged with `## [HH:MM:SS]` timestamps. Multiple files transcribe in parallel.

Works on any file — no naming convention required. Output filenames mirror the source (`report.pdf → report.md`, `talk.mp4 → talk.md`).

## Install

```bash
pipx install .        # or: pip install -e .
```

`faster-whisper` needs **ffmpeg** available on your system (`brew install ffmpeg`). It is only loaded when there is video or audio to transcribe.

## Usage

```bash
# Convert everything under the current directory into ./md
mdfy

# Specific folders and files; types are picked by extension
mdfy ./course ./slides/intro.pptx -o ./notes

# One type only (also recursive)
mdfy pdf ./papers
mdfy pptx "Unit 1 Lecture.pptx"
mdfy video ./lectures --model small --workers 6

# See what would happen first
mdfy --dry-run
```

`mdfied` is installed as an alias for `mdfy`, so older commands such as `mdfied pdf report.pdf` still work.

### Directory mode

Folders are walked recursively and the output mirrors the tree under `-o` (default `./md`):

```
course/                         md/
├── week1/                      ├── week1/
│   ├── slides.pptx      →      │   ├── slides.md
│   └── reading.pdf      →      │   └── reading.md
├── lecture.mp4          →      ├── lecture.md
├── report.pdf           →      ├── report.pdf.md
│                               ├── report.pdf_files/
└── report.pptx          →      └── report.pptx.md
```

Paths in the output are relative to each folder you pass; a file passed directly lands at the top of the output dir.

### Images

Each document's images go in a `<name>_files/` folder next to its Markdown (`report.md` → `report_files/`, `report.pdf.md` → `report.pdf_files/`) and are linked with relative, URL-encoded paths, so the folder can be moved as a unit:

```markdown
## Page 3 — Results

![image on page 3](Unit%201%20Notes_files/p3-2.png)
```

- **PDF:** each image object is saved once, however many pages use it, and identical images are stored once. JPEG and PNG in RGB or grey are kept as they are; CMYK, images with transparency masks, JPEG 2000 and other encodings are converted to PNG so they display everywhere. Images under 48×48 px or thinner than 8 px (rules, bullets) are skipped; `--all-images` keeps them.
- **PowerPoint:** pictures are copied in their original format (PNG, JPEG, GIF, BMP, TIFF, WebP), once per deck. A picture's alt text becomes the Markdown alt text. EMF/WMF and SVG, files over 32 MB, and files whose content doesn't match their extension are noted in the text but not copied. Names are sanitized, so nothing from inside the file can write outside the images folder.
- `--no-images` restores text-only `[IMAGE: ...]` notes and writes no files.

### What mdfy writes, and what it deletes

`md/.mdfy-manifest.json` records every Markdown file and image mdfy writes, with the source it came from, that source's size and modification time, and the options used. It is what makes runs incremental, and it is the only list mdfy will ever delete from:

- An output is up to date when its source and options are unchanged and its images are all present. Changing `--no-images`/`--all-images`, or deleting an image, reconverts it; reconverting removes images the new run no longer produces.
- When a name collision appears or goes away (`report.md` becomes `report.pdf.md`), the old Markdown file and its images are removed after the new one is written.
- `--prune` removes outputs whose source was deleted or is now ignored. It only looks at sources under the paths you gave and of the types being converted, and `--dry-run` lists what it would remove.
- Files mdfy didn't write are never touched, and an output edited since mdfy wrote it is left in place with a warning.

Output written by mdfy 0.2 has no manifest entries, so documents are converted again once (now with images); transcripts newer than their media are kept.

- **Name collisions.** When files in one folder share a name (`report.pdf`, `report.pptx`), each keeps its extension (`report.pdf.md`, `report.pptx.md`) instead of overwriting the other. Names are compared case-insensitively. The rule considers every supported type, so `mdfy pdf` and `mdfy` name a file the same way.
- **Incremental.** A file already converted from an unchanged source is skipped, and the summary says how many were. `--force` converts them again. This matters most for video.
- **Never scanned:** the output directory, `.git` and other version-control folders, and hidden files and folders (include those with `--hidden`). Directory symlinks are not followed, so a symlink loop can't hang the walk.
- **Failures** are reported per file and don't stop the run. The exit code is non-zero if anything failed, and a summary per type is printed at the end.
- Video and audio found anywhere in the tree go to the parallel transcriber as one batch.

### `.mdfyignore`

Put a `.mdfyignore` in any folder to skip paths. It uses gitignore syntax and, as in git, applies to its own folder and everything below it, with deeper files taking precedence:

```gitignore
# skip every drafts folder
drafts/

# only the archive at the top level
/archive/

# all decks under handouts, except the final one
handouts/**/*.pptx
!handouts/**/final.pptx

# large recordings
*.mkv
```

`--ignore PATTERN` adds a pattern from the command line (repeatable; it takes precedence over the files). `--no-ignore` stops reading `.mdfyignore` files. A file named directly on the command line is always converted.

### Options

| Flag | Default | Description |
|---|---|---|
| `-o`, `--output` | `./md` | output directory |
| `-f`, `--force` | off | reconvert files whose Markdown is up to date |
| `-n`, `--dry-run` | off | list what would be converted, skipped, and ignored |
| `--hidden` | off | include hidden files and folders |
| `--ignore PATTERN` | | extra gitignore-style pattern (repeatable) |
| `--no-ignore` | off | don't read `.mdfyignore` files |
| `--prune` | off | remove outputs whose sources were deleted or are now ignored |
| `--no-images` | off | note images in the text instead of saving them |
| `--all-images` | off | keep tiny PDF images too |

Video options (on `mdfy` and `mdfy video`):

| Flag | Default | Description |
|---|---|---|
| `--model` | `base` | whisper size: `tiny`, `base`, `small`, `medium`, `large` |
| `--workers` | `4` | files transcribed in parallel |
| `--threads` | `4` | CPU threads per worker |
| `--chunk-size` | `200` | target words per chunk |
| `--chunk-overlap` | `30` | word overlap between adjacent chunks |

## Library use

```python
from mdfied import pdf_to_markdown, pptx_to_markdown, video_to_markdown

pdf_to_markdown("report.pdf", "out/")
pptx_to_markdown("lecture.pptx", "out/")
video_to_markdown("talk.mp4", "out/", model_size="small")
```

Each also takes `out_path=` to choose the output file directly; the PDF and PowerPoint functions take `images=False` for text-only notes.

## Supported inputs

- **PDF:** `.pdf`
- **PowerPoint:** `.pptx .pptm .ppsx .potx`
- **Media:** `.mp4 .mkv .mov .webm .avi .m4v .mp3 .wav .m4a .flac .ogg .aac` (anything ffmpeg can decode)

## Development

```bash
pip install -e '.[test]'
pytest
```
