import os
import sys
import time

import pytest

from helpers import make_pdf, make_pptx, simple_pptx, slide_xml, text_box

from mdfied import cli
from mdfied import video as video_mod


def touch(path, data=b""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def tree(tmp_path, *rels):
    """Create inputs by relative path; .pdf/.pptx get real content."""
    for rel in rels:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.suffix == ".pdf":
            make_pdf(p, p.stem)
        elif p.suffix == ".pptx":
            simple_pptx(p, p.stem)
        else:
            p.write_bytes(b"x")


def outputs(root):
    if not root.exists():
        return []
    return sorted(
        str(p.relative_to(root)).replace(os.sep, "/")
        for p in root.rglob("*") if p.is_file() and p.name != ".mdfy-manifest.json"
    )


@pytest.fixture
def fake_video(monkeypatch):
    """Replace the transcriber; record each batch handed to transcribe_all."""
    batches = []
    real = video_mod.transcribe_all

    def fake_one(path, out_dir, model_size=None, cpu_threads=None,
                 chunk_size=None, chunk_overlap=None, out_path=None):
        if "bad" in os.path.basename(path):
            raise RuntimeError("cannot decode")
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w") as f:
            f.write("# t\n")
        return os.path.basename(path), 3

    def spy(paths, *a, **kw):
        batches.append(list(paths))
        return real(paths, *a, **kw)

    monkeypatch.setattr(video_mod, "video_to_markdown", fake_one)
    monkeypatch.setattr(video_mod, "transcribe_all", spy)
    return batches


def run(tmp_path, monkeypatch, *argv):
    monkeypatch.chdir(tmp_path)
    return cli.main(list(argv))


# --- discovery and output layout -------------------------------------------

def test_no_args_converts_cwd_recursively(tmp_path, monkeypatch):
    tree(tmp_path, "top.pdf", "a/b/report.pdf", "a/deck.pptx", "notes.txt")
    assert run(tmp_path, monkeypatch) == 0
    assert outputs(tmp_path / "md") == ["a/b/report.md", "a/deck.md", "top.md"]


def test_dir_argument_mirrors_relative_to_that_dir(tmp_path, monkeypatch):
    tree(tmp_path, "papers/x.pdf", "papers/sub/y.pdf")
    assert run(tmp_path, monkeypatch, "papers", "-o", "out") == 0
    assert outputs(tmp_path / "out") == ["sub/y.md", "x.md"]


def test_file_arguments_land_flat(tmp_path, monkeypatch):
    tree(tmp_path, "a/b/x.pdf")
    assert run(tmp_path, monkeypatch, "a/b/x.pdf") == 0
    assert outputs(tmp_path / "md") == ["x.md"]


def test_output_dir_inside_tree_is_not_scanned(tmp_path, monkeypatch):
    tree(tmp_path, "x.pdf", "md/old.pdf", "out/other.pdf")
    run(tmp_path, monkeypatch)
    assert outputs(tmp_path / "md") == ["old.pdf", "out/other.md", "x.md"]

    run(tmp_path, monkeypatch, "-o", "out")
    assert "other.md" not in outputs(tmp_path / "out")


def test_vcs_and_hidden_skipped_unless_hidden_flag(tmp_path, monkeypatch):
    tree(tmp_path, ".git/x.pdf", ".hg/y.pdf", ".hidden/z.pdf", ".dot.pdf", "ok.pdf")
    run(tmp_path, monkeypatch)
    assert outputs(tmp_path / "md") == ["ok.md"]
    run(tmp_path, monkeypatch, "--hidden", "-o", "md2")
    assert outputs(tmp_path / "md2") == [".dot.md", ".hidden/z.md", "ok.md"]


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks")
def test_symlink_loop_terminates_and_dir_links_not_followed(tmp_path, monkeypatch):
    tree(tmp_path, "a/x.pdf")
    os.symlink(tmp_path, tmp_path / "a" / "loop")
    os.symlink(tmp_path / "a", tmp_path / "alias")
    assert run(tmp_path, monkeypatch) == 0
    assert outputs(tmp_path / "md") == ["a/x.md"]


# --- ignore files -----------------------------------------------------------

def test_ignore_semantics(tmp_path, monkeypatch):
    tree(
        tmp_path,
        "drafts/a.pdf",            # dir-only pattern
        "x/drafts.pdf",            # a file named like the dir: kept
        "old.pdf", "keep/old.pdf", # anchored /old.pdf only at the root
        "big/one.pptx", "big/keep.pptx",  # negation
        "deep/a/b/tmp.pdf", "tmp.pdf",    # deep/**/tmp.pdf
        "sub/local.pdf", "sub/kept.pdf", "local.pdf",  # nested file
        "sub/inner/local.pdf",
    )
    (tmp_path / ".mdfyignore").write_text(
        "# comment\ndrafts/\n/old.pdf\nbig/*.pptx\n!big/keep.pptx\ndeep/**/tmp.pdf\n"
    )
    (tmp_path / "sub" / ".mdfyignore").write_text("/local.pdf\n")
    run(tmp_path, monkeypatch)
    assert outputs(tmp_path / "md") == [
        "big/keep.md", "keep/old.md", "local.md", "sub/inner/local.md",
        "sub/kept.md", "tmp.md", "x/drafts.md",
    ]


def test_nested_negation_overrides_parent(tmp_path, monkeypatch):
    tree(tmp_path, "a/x.pdf", "a/y.pdf", "b/x.pdf")
    (tmp_path / ".mdfyignore").write_text("x.pdf\n")
    (tmp_path / "a" / ".mdfyignore").write_text("!x.pdf\n")
    run(tmp_path, monkeypatch)
    assert outputs(tmp_path / "md") == ["a/x.md", "a/y.md"]


def test_ignored_dir_cannot_be_reincluded(tmp_path, monkeypatch):
    # As in git: once a directory is excluded, nothing inside it is read.
    tree(tmp_path, "build/x.pdf")
    (tmp_path / ".mdfyignore").write_text("build/\n!build/x.pdf\n")
    run(tmp_path, monkeypatch)
    assert outputs(tmp_path / "md") == []


def test_cli_ignore_and_no_ignore(tmp_path, monkeypatch):
    tree(tmp_path, "a.pdf", "b.pdf", "s/c.pptx")
    (tmp_path / ".mdfyignore").write_text("a.pdf\n")
    run(tmp_path, monkeypatch, "--ignore", "*.pptx", "--ignore", "b.pdf")
    assert outputs(tmp_path / "md") == []
    run(tmp_path, monkeypatch, "--no-ignore", "-o", "md2")
    assert outputs(tmp_path / "md2") == ["a.md", "b.md", "s/c.md"]


def test_explicit_file_is_not_ignored(tmp_path, monkeypatch):
    tree(tmp_path, "a.pdf")
    (tmp_path / ".mdfyignore").write_text("a.pdf\n")
    run(tmp_path, monkeypatch, "a.pdf")
    assert outputs(tmp_path / "md") == ["a.md"]


# --- collisions -------------------------------------------------------------

def test_collisions_keep_extension(tmp_path, monkeypatch):
    tree(tmp_path, "report.pdf", "report.pptx", "Other.pdf", "other.pptx",
         "solo.pdf", "sub/report.pdf")
    run(tmp_path, monkeypatch)
    assert outputs(tmp_path / "md") == [
        "Other.pdf.md", "other.pptx.md", "report.pdf.md", "report.pptx.md",
        "solo.md", "sub/report.md",
    ]


def test_collision_naming_independent_of_subcommand(tmp_path, monkeypatch):
    tree(tmp_path, "report.pdf", "report.pptx")
    run(tmp_path, monkeypatch, "pdf")
    assert outputs(tmp_path / "md") == ["report.pdf.md"]


def test_two_roots_claiming_one_output_fail_loudly(tmp_path, monkeypatch, capsys):
    tree(tmp_path, "a/x.pdf", "b/x.pdf")
    # Same folder in the output and same stem, so both keep the extension and
    # still collide; the second is refused rather than overwriting the first.
    assert run(tmp_path, monkeypatch, "a", "b") == 1
    assert outputs(tmp_path / "md") == ["x.pdf.md"]
    assert "already taken" in capsys.readouterr().err


# --- incremental, force, dry-run --------------------------------------------

def test_incremental_skip_and_force(tmp_path, monkeypatch, capsys):
    tree(tmp_path, "a.pdf", "b.pptx")
    run(tmp_path, monkeypatch)
    out = tmp_path / "md" / "a.md"
    first = out.stat().st_mtime_ns
    capsys.readouterr()

    run(tmp_path, monkeypatch)
    assert "Skipped 2 up-to-date" in capsys.readouterr().out
    assert out.stat().st_mtime_ns == first

    future = time.time() + 10
    os.utime(tmp_path / "a.pdf", (future, future))
    run(tmp_path, monkeypatch)
    text = capsys.readouterr().out
    assert "pdf    1 converted" in text and "Skipped 1" in text

    run(tmp_path, monkeypatch, "--force")
    assert "Skipped" not in capsys.readouterr().out


def test_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    tree(tmp_path, "a.pdf", "s/b.pptx", "junk/c.pdf")
    (tmp_path / ".mdfyignore").write_text("junk/\n")
    assert run(tmp_path, monkeypatch, "--dry-run") == 0
    text = capsys.readouterr().out
    assert not (tmp_path / "md").exists()
    assert f"convert  {os.path.join('.', 'a.pdf')} -> {os.path.join('./md', 'a.md')}" in text
    assert "ignore   " in text and "junk" in text and ".mdfyignore" in text
    assert "2 to convert, 0 up to date, 1 ignored" in text

    run(tmp_path, monkeypatch)
    capsys.readouterr()
    run(tmp_path, monkeypatch, "-n")
    assert "0 to convert, 2 up to date" in capsys.readouterr().out


# --- failures and summary ---------------------------------------------------

def test_one_failure_warns_continues_and_exits_nonzero(tmp_path, monkeypatch, capsys):
    tree(tmp_path, "good.pdf", "deck.pptx")
    touch(tmp_path / "broken.pdf", b"not a pdf")
    touch(tmp_path / "broken.pptx", b"not a zip")
    assert run(tmp_path, monkeypatch) == 1
    cap = capsys.readouterr()
    assert outputs(tmp_path / "md") == ["deck.md", "good.md"]
    assert "pdf    1 converted (1 pages), 0 up to date, 1 failed" in cap.out
    assert "pptx   1 converted (1 slides), 0 up to date, 1 failed" in cap.out


def test_missing_path_is_nonzero(tmp_path, monkeypatch):
    tree(tmp_path, "a.pdf")
    assert run(tmp_path, monkeypatch, "a.pdf", "nope") == 1


def test_subcommand_filters_and_recurses(tmp_path, monkeypatch, capsys):
    tree(tmp_path, "a.pdf", "d/b.pptx", "d/e/c.pptx")
    assert run(tmp_path, monkeypatch, "pptx") == 0
    assert outputs(tmp_path / "md") == ["d/b.md", "d/e/c.md"]
    run(tmp_path, monkeypatch, "pptx", "a.pdf")
    assert "unsupported extension" in capsys.readouterr().err


# --- video ------------------------------------------------------------------

def test_videos_go_through_one_batch(tmp_path, monkeypatch, fake_video, capsys):
    tree(tmp_path, "a.pdf", "v/one.mp4", "v/two.mp3", "v/deep/three.mkv", "v/bad.wav")
    assert run(tmp_path, monkeypatch, "--workers", "2") == 1
    assert len(fake_video) == 1 and len(fake_video[0]) == 4
    assert outputs(tmp_path / "md") == [
        "a.md", "v/deep/three.md", "v/one.md", "v/two.md",
    ]
    text = capsys.readouterr().out
    assert "video  3 converted (9 chunks), 0 up to date, 1 failed" in text

    fake_video.clear()
    run(tmp_path, monkeypatch, "video")
    assert fake_video == [[os.path.join(".", "v", "bad.wav")]]


def test_faster_whisper_not_imported_for_documents(tmp_path, monkeypatch):
    tree(tmp_path, "a.pdf")
    run(tmp_path, monkeypatch)
    assert "faster_whisper" not in sys.modules


# --- entry points -----------------------------------------------------------

def test_mdfy_and_mdfied_entry_points(capsys):
    tomllib = pytest.importorskip("tomllib")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "pyproject.toml"), "rb") as f:
        scripts = tomllib.load(f)["project"]["scripts"]
    assert scripts["mdfy"] == scripts["mdfied"] == "mdfied.cli:main"

    with pytest.raises(SystemExit) as e:
        cli.main(["--help"])
    assert e.value.code == 0
    assert capsys.readouterr().out.startswith("usage: mdfy")


def test_intermixed_options_and_paths(tmp_path, monkeypatch):
    tree(tmp_path, "a/x.pdf", "b/y.pdf")
    assert run(tmp_path, monkeypatch, "a", "-o", "out", "b") == 0
    assert outputs(tmp_path / "out") == ["x.md", "y.md"]


@pytest.mark.parametrize("ext", [".pptm", ".ppsx", ".potx"])
def test_pptx_family_extensions_in_cli(tmp_path, monkeypatch, ext):
    make_pptx(tmp_path / f"deck{ext}", [slide_xml([text_box("Hi", ph="title")])])
    monkeypatch.chdir(tmp_path)
    assert cli.main([]) == 0
    assert (tmp_path / "md" / "deck.md").read_text().startswith("# deck\n\n## Slide 1 — Hi")
