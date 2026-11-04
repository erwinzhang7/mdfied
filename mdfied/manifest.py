"""
manifest.py — record of what mdfy wrote into an output directory.

`<out>/.mdfy-manifest.json` maps each Markdown output (path relative to the
output dir) to the source it came from, that source's size and mtime, the
options it was converted with, the output's own mtime, and its image files.
It decides when an output is up to date, and it is the only thing that lets
mdfy delete a file: an output is removed only if the manifest lists it and the
file hasn't changed since mdfy wrote it.
"""

import json
import os

MANIFEST_NAME = ".mdfy-manifest.json"
VERSION = 1


def _within(root, path):
    root = os.path.abspath(root)
    path = os.path.abspath(path)
    return path != root and path.startswith(root + os.sep)


class Manifest:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.path = os.path.join(out_dir, MANIFEST_NAME)
        self.outputs = {}
        self.dirty = False
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("version") == VERSION:
                self.outputs = {
                    k: v for k, v in data.get("outputs", {}).items()
                    if isinstance(v, dict)
                }
        except (OSError, ValueError):
            pass

    def abs(self, rel):
        return os.path.join(self.out_dir, *rel.split("/"))

    def get(self, rel):
        return self.outputs.get(rel)

    def record(self, rel, kind, source, src_stat, opts, assets):
        out = self.abs(rel)
        self.outputs[rel] = {
            "source": os.path.realpath(source),
            "kind": kind,
            "size": src_stat.st_size,
            "mtime_ns": src_stat.st_mtime_ns,
            "opts": opts,
            "out_mtime_ns": os.stat(out).st_mtime_ns,
            "assets": list(assets),
        }
        self.dirty = True

    def up_to_date(self, rel, source, opts):
        e = self.outputs.get(rel)
        if not e:
            return False
        try:
            st = os.stat(source)
        except OSError:
            return False
        if not os.path.isfile(self.abs(rel)):
            return False
        return (
            e.get("source") == os.path.realpath(source)
            and e.get("size") == st.st_size
            and e.get("mtime_ns") == st.st_mtime_ns
            and e.get("opts") == opts
            and all(os.path.isfile(self.abs(a)) for a in e.get("assets", []))
        )

    def owned_unchanged(self, rel):
        """True if ``rel`` is listed and still exactly as mdfy wrote it."""
        e = self.outputs.get(rel)
        if not e:
            return False
        try:
            return os.stat(self.abs(rel)).st_mtime_ns == e.get("out_mtime_ns")
        except OSError:
            return False

    def remove_assets(self, rel, keep=()):
        """Delete listed image files of ``rel`` that are not in ``keep``."""
        e = self.outputs.get(rel) or {}
        dirs = set()
        for a in e.get("assets", []):
            if a in keep:
                continue
            path = self.abs(a)
            if not _within(self.out_dir, path):
                continue  # a hand-edited manifest can't point outside
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            dirs.add(os.path.dirname(path))
        for d in dirs:
            self._rmdir_up(d)

    def remove(self, rel):
        """Delete an owned output and its images; drop it from the manifest."""
        path = self.abs(rel)
        if _within(self.out_dir, path):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            self.remove_assets(rel)
            self._rmdir_up(os.path.dirname(path))
        self.outputs.pop(rel, None)
        self.dirty = True

    def forget(self, rel):
        if self.outputs.pop(rel, None) is not None:
            self.dirty = True

    def _rmdir_up(self, d):
        """Remove ``d`` and its parents while empty, stopping at the output dir."""
        while _within(self.out_dir, d):
            try:
                os.rmdir(d)
            except OSError:
                return
            d = os.path.dirname(d)

    def save(self):
        if not self.dirty:
            return
        os.makedirs(self.out_dir, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": VERSION, "outputs": self.outputs}, f,
                      indent=1, sort_keys=True)
        os.replace(tmp, self.path)
        self.dirty = False
