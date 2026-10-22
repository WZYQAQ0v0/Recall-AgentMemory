"""Runtime preflight, package integrity and owned-service HTTP checks."""
from __future__ import annotations

import hashlib
import json
import re
import socket
import subprocess
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from .portable import read_json

LOCAL_HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))

PROBE = """import json,sys,sqlite3
con=sqlite3.connect(':memory:')
try:
 con.execute('CREATE VIRTUAL TABLE probe USING fts5(text)')
 fts=True
except sqlite3.Error:
 fts=False
print(json.dumps({'python':list(sys.version_info[:3]),'sqlite':sqlite3.sqlite_version,'fts5':fts,'executable':sys.executable}))
"""


def probe_python(executable: str) -> dict:
    result = subprocess.run([executable, "-B", "-c", PROBE], capture_output=True, text=True, encoding="utf-8", timeout=20)
    if result.returncode:
        raise RuntimeError("Python could not start: " + result.stderr.strip()[:400])
    info = json.loads(result.stdout)
    info["ready"] = tuple(info["python"]) >= (3, 9) and info["fts5"]
    if not info["ready"]:
        raise RuntimeError("Python 3.9+ with SQLite FTS5 is required")
    return info


def verify_package(root: Path) -> dict:
    manifest = read_json(root / "package-manifest.json")
    if manifest.get("name") != "recall-agent-package" or manifest.get("schema") != 1 or not isinstance(manifest.get("files"), dict):
        raise ValueError("This is not a Recall distribution manifest")
    required = {"recallctl.py", "app.py", "hook_capture.py", "recall/__init__.py", "recall/cli.py", "recall/core.py", "recall/web/index.html"}
    if not required.issubset(manifest["files"]):
        raise ValueError("The Recall package manifest is incomplete")
    errors = []
    for relative, digest in manifest["files"].items():
        file = root / relative
        if Path(relative).is_absolute() or ".." in Path(relative).parts or "\\" in relative or file.is_symlink() or not file.resolve().is_relative_to(root.resolve()):
            raise ValueError("Unsafe package path: " + relative)
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("Invalid package checksum: " + relative)
        if not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != digest:
            errors.append(relative)
    if errors:
        raise RuntimeError("Package files are missing or changed: " + ", ".join(errors[:10]))
    return manifest


def free_port(preferred: int, attempts: int = 50) -> int:
    if not 1024 <= preferred <= 65535:
        raise ValueError("Port must be between 1024 and 65535")
    for port in range(preferred, min(65536, preferred + attempts)):
        with socket.socket() as sock:
            if os.name != "nt":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No available localhost port near " + str(preferred))


def get_json(port: int, path: str, timeout: float = 1) -> dict | None:
    try:
        with LOCAL_HTTP.open(f"http://127.0.0.1:{port}" + path, timeout=timeout) as response:
            result = json.load(response)
            return result if isinstance(result, dict) else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def owned_service(state: dict) -> dict | None:
    info = get_json(state["port"], "/api/bootstrap")
    if info and info.get("app") == "session-atlas" and info.get("instance") == state["install_id"] \
            and Path(info.get("data_dir", "")).resolve() == Path(state["data_dir"]).resolve() \
            and Path(info.get("app_dir", "")).resolve() == Path(state["app_dir"]).resolve():
        return info
    return None


def wait_ready(state: dict, timeout: float = 12) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = owned_service(state)
        if info and info.get("version") == state.get("version", info.get("version")):
            return info
        time.sleep(0.15)
    raise RuntimeError("Recall did not become ready; inspect " + state["log_dir"])


def shutdown_owned(state: dict):
    info = owned_service(state)
    if not info:
        return
    request = urllib.request.Request(f"http://127.0.0.1:{state['port']}/api/shutdown", data=b"{}", method="POST",
                                    headers={"Content-Type": "application/json", "X-Atlas-Token": info["token"]})
    with LOCAL_HTTP.open(request, timeout=3):
        pass
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and owned_service(state):
        time.sleep(0.1)
