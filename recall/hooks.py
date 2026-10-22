"""Preserve other integrations while adding/removing only this installation's hooks."""
from __future__ import annotations

import copy
import json
import os
import shlex
import subprocess
import sys
import uuid
from pathlib import Path

from .portable import read_json, write_json

EVENTS = ["SessionStart", "UserPromptSubmit", "Stop"]
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
HELPER = PACKAGE_ROOT / "hook_capture.py"


def command_line(arguments: list[str], system: str | None = None) -> str:
    system = system or ("Windows" if os.name == "nt" else "Unix")
    if system == "Windows":
        return subprocess.list2cmdline(arguments)
    return " ".join(shlex.quote(argument) for argument in arguments)


def owned_hook(hook: dict, helper: Path, install_id: str = "") -> bool:
    command = str(hook.get("command", ""))
    if install_id:
        return "--capture-id" in command and install_id in command
    return str(helper) in command


def edit_config(original: dict, helper: Path, data: Path, python: str, install_id: str,
                client: str = "Trae", system: str | None = None, remove: bool = False) -> dict:
    if not isinstance(original, dict) or not isinstance(original.get("hooks", {}), dict):
        raise ValueError("Trae hook configuration must contain a hooks object")
    updated = copy.deepcopy(original)
    for event in EVENTS:
        if remove and event not in updated.get("hooks", {}):
            continue
        entries = updated.setdefault("hooks", {}).setdefault(event, [])
        if not isinstance(entries, list):
            raise ValueError("Hook event entries must be a list: " + event)
        kept = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("hooks"), list):
                kept.append(entry)
                continue
            remaining = [hook for hook in entry["hooks"] if not (isinstance(hook, dict) and owned_hook(hook, helper, install_id))]
            if remaining or len(remaining) == len(entry["hooks"]):
                entry["hooks"] = remaining
                kept.append(entry)
        if not remove:
            arguments = [python, "-B", str(helper), "--event", event, "--data-dir", str(data)]
            if install_id:
                arguments += ["--capture-id", install_id, "--client", client]
            kept.append({"hooks": [{"type": "command", "command": command_line(arguments, system)}]})
        if remove and not kept and entries:
            updated["hooks"].pop(event, None)
        else:
            updated["hooks"][event] = kept
    return updated


def plan(config_path: Path, directory: Path) -> tuple[dict, dict]:
    """Compatibility helper for application tests and a read-only preview."""
    original = read_json(config_path) if config_path.exists() else {"version": 1, "hooks": {}}
    return original, edit_config(original, HELPER, directory, sys.executable, "")


def register_targets(targets: list[dict], helper: Path, data: Path, python: str, install_id: str,
                     system: str, backup_dir: Path) -> list[dict]:
    applied = []
    try:
        for target in targets:
            path = Path(target["config_path"])
            original = read_json(path) if path.exists() else {"version": 1, "hooks": {}}
            existed = path.exists()
            updated = edit_config(original, helper, data, python, install_id, target["client"], system)
            if updated != original:
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup = backup_dir / (path.parent.name.replace(".", "_") + "-" + uuid.uuid4().hex[:8] + ".json")
                write_json(backup, original)
                # Re-read before writing so a concurrent integration is not overwritten.
                latest = read_json(path) if path.exists() else {"version": 1, "hooks": {}}
                updated = edit_config(latest, helper, data, python, install_id, target["client"], system)
                write_json(path, updated)
            applied.append({"client": target["client"], "config_path": str(path), "helper_path": str(helper),
                            "events": EVENTS, "config_existed": existed})
        write_json(data / "trae-hooks-installed.json", {"install_id": install_id, "configs": applied})
        return applied
    except Exception:
        remove_targets(applied, helper, install_id)
        raise


def remove_targets(targets: list[dict], helper: Path, install_id: str) -> list[dict]:
    results = []
    for target in targets:
        path = Path(target["config_path"])
        if not path.exists():
            continue
        original = read_json(path)
        updated = edit_config(original, helper, Path("."), "", install_id, remove=True)
        if updated != original:
            write_json(path, updated)
        results.append({"config_path": str(path), "removed": updated != original})
    return results


def verify_targets(targets: list[dict], helper: Path, install_id: str) -> list[dict]:
    results = []
    for target in targets:
        path = Path(target["config_path"])
        try:
            config = read_json(path)
            events = {event: sum(1 for entry in config.get("hooks", {}).get(event, []) if isinstance(entry, dict)
                        for hook in entry.get("hooks", []) if isinstance(hook, dict) and owned_hook(hook, helper, install_id)) for event in EVENTS}
            results.append({"client": target["client"], "configured": all(count == 1 for count in events.values()), "events": events})
        except (OSError, ValueError, TypeError) as error:
            results.append({"client": target["client"], "configured": False, "error": type(error).__name__})
    return results
