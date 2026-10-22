"""Report configured features, synthetic pipeline proof and observed callbacks separately."""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path

from .core import Indexer, Store
from .hooks import verify_targets
from .paths import Layout
from .portable import read_json
from .runtime import get_json, owned_service, probe_python, verify_package
from .services import startup_health


def wait_initial_scan(state: dict, timeout: float = 20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = get_json(state["port"], "/api/status")
        if status and status["index"].get("last_scan") and not status["index"]["running"]:
            return status
        time.sleep(0.2)
    return get_json(state["port"], "/api/status")


def pipeline_smoke(state: dict) -> dict:
    # This directory is never a real data/source directory, and is removed at exit.
    with tempfile.TemporaryDirectory(prefix="recall-doctor-") as temporary:
        directory = Path(temporary)
        needle = "recall synthetic pipeline needle"
        for event, payload in [
            ("SessionStart", {"session_id": "synthetic-doctor-session", "cwd": str(directory / "project")}),
            ("UserPromptSubmit", {"session_id": "synthetic-doctor-session", "prompt": needle}),
            ("Stop", {"session_id": "synthetic-doctor-session", "last_assistant_message": "根因：连接池等待导致超时。"}),
        ]:
            command = [state["python"], "-B", str(Path(state["app_dir"]) / "hook_capture.py"), "--event", event,
                       "--data-dir", str(directory), "--capture-id", state["install_id"], "--client", "synthetic-doctor"]
            result = subprocess.run(command, input=json.dumps(payload, ensure_ascii=False), capture_output=True,
                                    text=True, encoding="utf-8", timeout=10)
            if result.returncode:
                raise RuntimeError("Synthetic capture process failed")
        store = Store(directory)
        indexer = Indexer(store, {})
        if not indexer.scan() or indexer.status()["errors"] or store.search(needle, provider="trae")["total"] != 1:
            raise RuntimeError("Synthetic capture/index/search did not pass")
        detail = store.detail("trae:synthetic-doctor-session")
        if not detail or detail["total_messages"] != 2 or "根因" not in detail["session"]["conclusion"]:
            raise RuntimeError("Synthetic summary or conversation detail did not pass")
        return {"passed": True, "scope": "synthetic_event_to_capture_index_search_summary", "real_client_callback": False}


def doctor(layout: Layout, state: dict | None = None, smoke: bool = True) -> dict:
    from .install import load_state
    errors, warnings = [], []
    report = {"app": "recall", "ready": False, "errors": errors, "warnings": warnings,
              "platform": layout.system, "prefix": str(layout.base)}
    try:
        state = state or load_state(layout, required=True)
    except Exception as error:
        errors.append(str(error))
        return report
    report["version"] = state["version"]
    report["url"] = "http://127.0.0.1:" + str(state["port"])
    try:
        report["runtime"] = probe_python(state["python"])
    except Exception as error:
        errors.append("runtime: " + str(error))
    try:
        manifest = verify_package(layout.app)
        report["package_integrity"] = {"passed": True, "files": len(manifest["files"]), "version": manifest["version"]}
    except Exception as error:
        errors.append("package: " + str(error))
    service = owned_service(state)
    report["service"] = {"running": bool(service), "owned": bool(service), "binding": "127.0.0.1", "instance": state["install_id"],
                         "version": service.get("version") if service else None}
    if not service:
        errors.append("Recall service is not running; run start or inspect the service logs")
    elif service.get("version") != state["version"]:
        errors.append("The running server version does not match the installed package; restart or reinstall")
    try:
        report["autostart"] = startup_health(state)
        if state["features"].get("autostart") and not report["autostart"]["enabled"]:
            errors.append("autostart: the managed startup file is missing or edited")
        if state["features"].get("autostart") and report["autostart"].get("loaded") is False:
            errors.append("autostart: the startup service is not loaded")
    except Exception as error:
        errors.append("autostart: " + str(error))
    status = get_json(state["port"], "/api/status") if service else None
    report["index"] = {key: status["index"].get(key) for key in ["running", "done", "total_files", "last_scan", "errors"]} if status else None
    report["counts"] = {key: status["stats"].get(key) for key in ["total", "messages", "providers"]} if status else None
    if status:
        report["sources"] = status["index"].get("sources", [])
        if status["index"].get("errors"):
            warnings.append("Some source files could not be read; inspect index.errors")
        if status["index"].get("running"):
            warnings.append("The first source scan is still in progress")
        if not status["stats"]["total"]:
            warnings.append("No conversations indexed yet; create a supported local session or import an export")
    report["trae_hooks"] = verify_targets(state.get("hooks", []), layout.app / "hook_capture.py", state["install_id"])
    if any(not target["configured"] for target in report["trae_hooks"]):
        errors.append("trae_hooks: one or more managed hook entries are missing")
    evidence = {}
    try:
        evidence = read_json(layout.data / "trae-capture-status.json")
    except (OSError, ValueError):
        pass
    observed = evidence.get("capture_id") == state["install_id"] and bool(evidence.get("last_message_at"))
    report["trae_live_events"] = {"observed": observed, "last_message_at": evidence.get("last_message_at", 0) if observed else 0,
                                  "status": "client_callback_observed" if observed else "awaiting_client_callback"}
    if state.get("hooks") and not observed:
        warnings.append("Trae hooks are configured; a real new-session callback has not been observed")
    report["trae_history"] = {"automatic_decryption": False, "method": "user_export_or_manual_import"}
    if smoke and report.get("package_integrity", {}).get("passed") and report.get("runtime", {}).get("ready"):
        try:
            report["pipeline_smoke"] = pipeline_smoke(state)
        except Exception as error:
            report["pipeline_smoke"] = {"passed": False, "real_client_callback": False}
            errors.append("pipeline: " + str(error))
    report["ready"] = not errors
    return report
