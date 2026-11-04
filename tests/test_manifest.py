import json
import os
import time

from helpers import make_image_pdf, make_pdf, simple_pptx

from mdfied import cli


def run(tmp_path, monkeypatch, *argv):
    monkeypatch.chdir(tmp_path)
    return cli.main(list(argv))


def manifest(tmp_path):
    return json.loads((tmp_path / "md" / ".mdfy-manifest.json").read_text())["outputs"]


def bump(path, seconds=10):
    t = time.time() + seconds
    os.utime(path, (t, t))


def test_manifest_records_outputs_and_assets(tmp_path, monkeypatch):
    (tmp_path / "a").mkdir()
    make_image_pdf(tmp_path / "a" / "s.pdf")
    run(tmp_path, monkeypatch)
    e = manifest(tmp_path)["a/s.md"]
    assert e["kind"] == "pdf" and e["opts"] == "img"
    assert e["source"] == os.path.realpath(tmp_path / "a" / "s.pdf")
    assert e["assets"] == ["a/s_files/p1-1.png", "a/s_files/p1-2.png"]


def test_missing_asset_or_option_change_reconverts(tmp_path, monkeypatch, capsys):
    make_image_pdf(tmp_path / "s.pdf")
    run(tmp_path, monkeypatch)
    capsys.readouterr()
    run(tmp_path, monkeypatch)
    assert "Skipped 1" in capsys.readouterr().out

    os.remove(tmp_path / "md" / "s_files" / "p1-2.png")
    run(tmp_path, monkeypatch)
    assert "pdf    1 converted" in capsys.readouterr().out
    assert (tmp_path / "md" / "s_files" / "p1-2.png").is_file()

    # Turning images off reconverts and removes the images mdfy wrote.
    run(tmp_path, monkeypatch, "--no-images")
    assert "pdf    1 converted" in capsys.readouterr().out
    assert not (tmp_path / "md" / "s_files").exists()
    assert manifest(tmp_path)["s.md"]["assets"] == []


def test_force_rewrites_assets(tmp_path, monkeypatch):
    make_image_pdf(tmp_path / "s.pdf")
    run(tmp_path, monkeypatch)
    img = tmp_path / "md" / "s_files" / "p1-1.png"
    img.write_bytes(b"tampered")
    run(tmp_path, monkeypatch, "--force")
    assert img.read_bytes().startswith(b"\x89PNG")


def test_reconversion_drops_images_that_went_away(tmp_path, monkeypatch):
    make_image_pdf(tmp_path / "s.pdf")
    run(tmp_path, monkeypatch, "--all-images")
    assert len(os.listdir(tmp_path / "md" / "s_files")) == 3
    run(tmp_path, monkeypatch)
    assert sorted(os.listdir(tmp_path / "md" / "s_files")) == ["p1-1.png", "p1-2.png"]


def test_collision_rename_removes_owned_stale_output(tmp_path, monkeypatch, capsys):
    make_image_pdf(tmp_path / "report.pdf")
    run(tmp_path, monkeypatch)
    assert (tmp_path / "md" / "report.md").is_file()
    assert (tmp_path / "md" / "report_files").is_dir()

    simple_pptx(tmp_path / "report.pptx")
    run(tmp_path, monkeypatch, "-n")
    assert "remove   ./md/report.md (renamed to report.pdf.md)" in capsys.readouterr().out
    assert (tmp_path / "md" / "report.md").is_file()

    run(tmp_path, monkeypatch)
    assert sorted(os.listdir(tmp_path / "md")) == [
        ".mdfy-manifest.json", "report.pdf.md", "report.pdf_files", "report.pptx.md",
    ]
    assert "report.md" not in manifest(tmp_path)

    # And back: deleting the deck renames report.pdf.md to report.md.
    os.remove(tmp_path / "report.pptx")
    run(tmp_path, monkeypatch)
    names = sorted(os.listdir(tmp_path / "md"))
    assert "report.md" in names and "report.pdf.md" not in names
    assert "report.pdf_files" not in names
    assert "report.pptx.md" in names  # stale, but only --prune removes it


def test_never_deletes_what_it_did_not_write(tmp_path, monkeypatch, capsys):
    make_pdf(tmp_path / "report.pdf")
    (tmp_path / "md").mkdir()
    (tmp_path / "md" / "report.md").write_text("mine")       # not in the manifest
    (tmp_path / "md" / "notes.md").write_text("also mine")
    simple_pptx(tmp_path / "report.pptx")
    run(tmp_path, monkeypatch, "--prune")
    assert (tmp_path / "md" / "report.md").read_text() == "mine"
    assert (tmp_path / "md" / "notes.md").read_text() == "also mine"


def test_edited_output_is_left_in_place(tmp_path, monkeypatch, capsys):
    make_pdf(tmp_path / "report.pdf")
    run(tmp_path, monkeypatch)
    edited = tmp_path / "md" / "report.md"
    edited.write_text("hand edits")
    bump(edited)
    simple_pptx(tmp_path / "report.pptx")
    run(tmp_path, monkeypatch)
    assert edited.read_text() == "hand edits"
    assert "changed after mdfy wrote it" in capsys.readouterr().err


def test_prune_deleted_and_ignored_sources(tmp_path, monkeypatch, capsys):
    (tmp_path / "keep").mkdir()
    (tmp_path / "gone").mkdir()
    make_image_pdf(tmp_path / "gone" / "a.pdf")
    make_pdf(tmp_path / "keep" / "b.pdf")
    make_pdf(tmp_path / "keep" / "c.pdf")
    run(tmp_path, monkeypatch)

    os.remove(tmp_path / "gone" / "a.pdf")
    (tmp_path / ".mdfyignore").write_text("c.pdf\n")
    run(tmp_path, monkeypatch)              # without --prune nothing is removed
    assert (tmp_path / "md" / "gone" / "a.md").is_file()
    capsys.readouterr()

    run(tmp_path, monkeypatch, "--prune", "--dry-run")
    out = capsys.readouterr().out
    assert "prune    ./md/gone/a.md (source deleted)" in out
    assert "prune    ./md/keep/c.md (source ignored)" in out
    assert (tmp_path / "md" / "gone" / "a.md").is_file()

    run(tmp_path, monkeypatch, "--prune")
    assert not (tmp_path / "md" / "gone").exists()     # md, images and empty dir
    assert sorted(os.listdir(tmp_path / "md" / "keep")) == ["b.md"]
    assert set(manifest(tmp_path)) == {"keep/b.md"}


def test_prune_is_scoped_to_the_paths_given(tmp_path, monkeypatch):
    for d in ("x", "y"):
        (tmp_path / d).mkdir()
        make_pdf(tmp_path / d / f"{d}.pdf")
    run(tmp_path, monkeypatch, "x", "y")
    os.remove(tmp_path / "y" / "y.pdf")
    run(tmp_path, monkeypatch, "x", "--prune")
    assert (tmp_path / "md" / "y.md").is_file()
    run(tmp_path, monkeypatch, "pptx", "y", "--prune")  # other type: untouched
    assert (tmp_path / "md" / "y.md").is_file()
    run(tmp_path, monkeypatch, "pdf", "y", "x", "--prune")
    assert not (tmp_path / "md" / "y.md").exists()


def test_tampered_manifest_cannot_delete_outside(tmp_path, monkeypatch):
    make_pdf(tmp_path / "a.pdf")
    run(tmp_path, monkeypatch)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    m = json.loads((tmp_path / "md" / ".mdfy-manifest.json").read_text())
    m["outputs"]["a.md"]["assets"] = ["../victim.txt"]
    m["outputs"]["../victim.txt"] = dict(m["outputs"]["a.md"], source=str(tmp_path / "nope.pdf"))
    (tmp_path / "md" / ".mdfy-manifest.json").write_text(json.dumps(m))
    run(tmp_path, monkeypatch, "--force", "--prune", "--no-images")
    assert victim.read_text() == "keep me"


def test_old_outputs_without_manifest(tmp_path, monkeypatch, capsys, ):
    # Output from mdfy 0.2 has no manifest entry: documents are redone once,
    # a transcript newer than its media is kept.
    make_pdf(tmp_path / "a.pdf")
    (tmp_path / "md").mkdir()
    (tmp_path / "md" / "a.md").write_text("old")
    bump(tmp_path / "md" / "a.md")
    run(tmp_path, monkeypatch)
    assert (tmp_path / "md" / "a.md").read_text().startswith("# a\n")
