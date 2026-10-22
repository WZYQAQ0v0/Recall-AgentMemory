#!/usr/bin/env python3
"""Build a source-only manifest/archive; private runtime files are never collected."""
from __future__ import annotations

import argparse
import hashlib
import json
import stat
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROOT_FILES = {"README.md", "AGENTS.md", "CLAUDE.md", "LICENSE", "pyproject.toml", ".gitignore",
              "install.sh", "install.command", "install.cmd", "install.ps1", "recallctl.py", "app.py", "hook_capture.py"}
TREES = {"recall": {".py", ".html", ".css", ".js", ".svg"}, "tests": {".py"}, "scripts": {".py"},
         "examples": {".json"}, "docs": {".md"}, ".github": {".yml"}}


def collect(root: Path) -> list[Path]:
    files = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if "__pycache__" in relative.parts or ".git" in relative.parts:
            continue
        accepted = relative.as_posix() in ROOT_FILES or (
            relative.parts[0] in TREES and path.suffix in TREES[relative.parts[0]])
        if accepted:
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("Distribution source contains a symbolic link: " + str(relative))
            if str(Path.home()).encode() in path.read_bytes() or b"session-atlas" + b"-data" in path.read_bytes():
                raise ValueError("Distribution source contains a private workspace reference: " + str(relative))
            files.append(path)
    missing = ROOT_FILES - {path.relative_to(root).as_posix() for path in files}
    if missing:
        raise ValueError("Required source files missing: " + ", ".join(sorted(missing)))
    return sorted(files)


def build_manifest(root: Path = ROOT) -> dict:
    # Do not execute/import source files when calculating distribution metadata.
    import ast
    tree = ast.parse((root / "recall" / "__init__.py").read_text(encoding="utf-8"))
    version = next(node.value.value for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets))
    files = collect(root)
    value = {"schema": 1, "name": "recall-agent-package", "version": version,
             "files": {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in files}}
    (root / "package-manifest.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    manifest = build_manifest()
    result = {"version": manifest["version"], "files": len(manifest["files"])}
    if args.archive:
        args.archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(args.archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for relative in [*manifest["files"], "package-manifest.json"]:
                path = ROOT / relative
                info = zipfile.ZipInfo(ROOT.name + "/" + relative)
                info.external_attr = (stat.S_IFREG | (0o755 if relative in {"install.sh", "install.command"} else 0o644)) << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())
        result.update(archive=str(args.archive.resolve()), bytes=args.archive.stat().st_size,
                      sha256=hashlib.sha256(args.archive.read_bytes()).hexdigest())
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
