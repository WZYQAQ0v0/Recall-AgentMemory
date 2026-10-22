"""Transactional folder installation with explicit ownership and retained data."""
from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import __version__
from . import hooks, services
from .paths import Layout, detect_sources
from .portable import locked_file, private_directory, read_json, write_json
from .runtime import free_port, owned_service, probe_python, verify_package

PACKAGE_ROOT = Path(__file__).resolve().parent.parent
OWNER_FILE = ".installation-owner.json"


def validate_layout(layout: Layout):
    if layout.base in {layout.home, Path(layout.base.anchor)}:
        raise ValueError("Choose a dedicated Recall installation directory")
    if any(ord(char) < 32 for char in str(layout.base) + str(layout.home)):
        raise ValueError("Installation paths cannot contain control characters")
    for path in [layout.app, layout.data, layout.config, layout.logs, layout.state_file, layout.roots_file]:
        if path.is_symlink():
            raise ValueError("A managed path is a symbolic link; kept: " + str(path))


def load_state(layout: Layout, required: bool = False) -> dict | None:
    validate_layout(layout)
    if not layout.state_file.exists():
        if required:
            raise RuntimeError("Recall is not installed at " + str(layout.base))
        return None
    state = read_json(layout.state_file)
    expected = {"app_dir": layout.app, "data_dir": layout.data, "roots_file": layout.roots_file,
                "log_dir": layout.logs, "prefix": layout.base, "home": layout.home}
    if state.get("app") != "recall" or state.get("schema") != 1 or state.get("system") != layout.system:
        raise RuntimeError("This directory does not belong to a compatible Recall installation")
    try:
        uuid.UUID(state["install_id"])
    except (KeyError, ValueError, TypeError):
        raise RuntimeError("Invalid Recall installation identity")
    for key, path in expected.items():
        if Path(state.get(key, "")).resolve() != path.resolve():
            raise RuntimeError("Installation registry path does not match: " + key)
    if state.get("service_label") != layout.service_label or not isinstance(state.get("port"), int) or not 1024 <= state["port"] <= 65535:
        raise RuntimeError("Installation registry has an invalid service or port")
    if state.get("startup"):
        descriptor = services.startup_descriptor(layout, state, state["startup"]["kind"])
        if any(state["startup"].get(key) != descriptor[key] for key in ["path", "label", "kind"]):
            raise RuntimeError("Installation registry points to an unrelated startup file")
    return state


def managed_app(layout: Layout, state: dict):
    marker = layout.app / OWNER_FILE
    if not marker.is_file() or marker.is_symlink():
        raise RuntimeError("Existing application directory has no Recall ownership marker; kept")
    owner = read_json(marker)
    if owner.get("install_id") != state["install_id"] or owner.get("app") != "recall":
        raise RuntimeError("Application directory belongs to another installation; kept")


@contextmanager
def installation_lock(layout: Layout):
    validate_layout(layout)
    private_directory(layout.base)
    with (layout.base / ".installation.lock").open("a+") as lock, locked_file(lock, timeout=10):
        yield


def plan_install(layout: Layout, source: Path = PACKAGE_ROOT, python: str | None = None,
                 port: int | None = None, interval: int = 30, no_autostart: bool = False,
                 no_hooks: bool = False) -> dict:
    validate_layout(layout)
    source = source.expanduser().resolve()
    manifest = verify_package(source)
    previous = load_state(layout)
    if layout.app.exists():
        if not previous:
            raise RuntimeError("An unrelated app directory exists; choose another prefix")
        managed_app(layout, previous)
    runtime = probe_python(python or sys.executable)
    detection = detect_sources(layout)
    roots = read_json(layout.roots_file) if layout.roots_file.exists() else {}
    if not isinstance(roots, dict) or any(not isinstance(value, str) for value in roots.values()):
        raise ValueError("sources.json must map source names to directory strings")
    for key, value in detection["roots"].items():
        roots.setdefault(key, value)
    preferred = port if port is not None else (previous["port"] if previous else 8767)
    port = preferred if previous and previous["port"] == preferred and owned_service(previous) else free_port(preferred)
    state = {"app": "recall", "schema": 1, "version": manifest["version"], "install_id": previous["install_id"] if previous else str(uuid.uuid4()),
             "prefix": str(layout.base), "home": str(layout.home), "system": layout.system, "python": runtime["executable"],
             "app_dir": str(layout.app), "data_dir": str(layout.data), "roots_file": str(layout.roots_file),
             "log_dir": str(layout.logs), "service_label": layout.service_label, "port": port, "interval": max(10, interval),
             "startup": copy.deepcopy(previous.get("startup", {})) if previous else {},
             "hooks": copy.deepcopy(previous.get("hooks", [])) if previous else [],
             "features": {"autostart": not no_autostart, "trae_hooks": not no_hooks},
             "created_at": previous.get("created_at", time.time()) if previous else time.time(), "status": "installing"}
    targets = detection["trae_clients"] if not no_hooks else []
    for target in targets:
        path = Path(target["config_path"])
        if path.is_symlink():
            raise RuntimeError("Trae hooks.json is a symbolic link; resolve its owner before installation")
        original = read_json(path) if path.exists() else {"version": 1, "hooks": {}}
        hooks.edit_config(original, layout.app / "hook_capture.py", layout.data, state["python"], state["install_id"], target["client"], layout.system)
    descriptor = services.startup_descriptor(layout, state) if not no_autostart else None
    if descriptor:
        services.check_registration(state, descriptor)
    elif previous and previous.get("startup") and not services.startup_owned(previous["startup"]):
        raise RuntimeError("An edited startup file cannot be removed automatically")
    return {"state": state, "runtime": runtime, "roots": roots, "detection": detection, "targets": targets,
            "startup": descriptor, "source": str(source), "manifest": manifest, "previous": previous,
            "url": "http://127.0.0.1:" + str(port), "data_retained": True}


def copy_package(source: Path, destination: Path, manifest: dict, install_id: str):
    private_directory(destination)
    for relative in [*manifest["files"], "package-manifest.json"]:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
    write_json(destination / OWNER_FILE, {"app": "recall", "install_id": install_id, "version": manifest["version"]})
    verify_package(destination)


def leave_code_directory(layout: Layout):
    # Windows cannot rename/delete a directory held as this process's cwd.
    try:
        inside = Path.cwd().resolve().is_relative_to(layout.app)
    except FileNotFoundError:
        inside = True
    if inside:
        os.chdir(layout.base)


def install(layout: Layout, **options) -> dict:
    with installation_lock(layout):
        plan = plan_install(layout, **options)
        state, previous = plan["state"], plan["previous"]
        old_state = copy.deepcopy(previous)
        was_running = bool(previous and owned_service(previous))
        old_roots = layout.roots_file.read_text(encoding="utf-8") if layout.roots_file.is_file() else None
        old_startup = None
        if previous and previous.get("startup") and services.startup_owned(previous["startup"]):
            info = previous["startup"]
            old_startup = {**info, "content": Path(info["path"]).read_text(encoding="utf-16" if info["kind"] == "windows-startup" else "utf-8")}
        stage = layout.base / (".app-staging-" + uuid.uuid4().hex)
        backup = layout.base / (".app-previous-" + uuid.uuid4().hex)
        replaced = False
        hooks_changed = False
        try:
            copy_package(Path(plan["source"]), stage, plan["manifest"], state["install_id"])
            for directory in [layout.config, layout.data, layout.logs]:
                private_directory(directory)
            leave_code_directory(layout)
            if previous:
                services.stop(previous)
            if layout.app.exists():
                layout.app.rename(backup)
            stage.rename(layout.app)
            replaced = True
            write_json(layout.roots_file, plan["roots"])
            write_json(layout.state_file, state)
            # Remove only this identity's old hooks before registering the detected
            # clients. This also removes integrations for clients no longer present.
            target_paths = {target["config_path"] for target in plan["targets"]}
            obsolete = [target for target in (previous or {}).get("hooks", []) if target["config_path"] not in target_paths]
            if obsolete:
                hooks.remove_targets(obsolete, layout.app / "hook_capture.py", state["install_id"])
            hooks_changed = True
            state["hooks"] = hooks.register_targets(plan["targets"], layout.app / "hook_capture.py", layout.data,
                                                    state["python"], state["install_id"], layout.system, layout.config / "hook-backups")
            write_json(layout.state_file, state)
            if plan["startup"]:
                services.register_startup(state, plan["startup"])
            else:
                if state.get("startup"):
                    services.unregister_startup(state)
                services.start(state)
            write_json(layout.state_file, state)
            from .doctor import doctor, wait_initial_scan
            wait_initial_scan(state, timeout=20)
            report = doctor(layout, state, smoke=True)
            if not report["ready"]:
                raise RuntimeError("Installation verification failed: " + "; ".join(report["errors"]))
            state["status"] = "installed"
            state["updated_at"] = time.time()
            write_json(layout.state_file, state)
            if backup.exists():
                shutil.rmtree(backup)
            return {"installed": True, "version": state["version"], "url": plan["url"], "prefix": str(layout.base),
                    "control": str(layout.app / "recallctl.py"), "python": state["python"], "doctor": report}
        except Exception as error:
            rollback_errors = []
            if replaced:
                try:
                    services.unregister_startup(state)
                except Exception as rollback_error:
                    rollback_errors.append("startup: " + str(rollback_error))
                if hooks_changed:
                    try:
                        hooks.remove_targets(plan["targets"] + state.get("hooks", []), layout.app / "hook_capture.py", state["install_id"])
                    except Exception as rollback_error:
                        rollback_errors.append("hooks: " + str(rollback_error))
                try:
                    managed_app(layout, state)
                    shutil.rmtree(layout.app)
                    if backup.exists():
                        backup.rename(layout.app)
                    if old_roots is not None:
                        from .portable import atomic_text
                        atomic_text(layout.roots_file, old_roots)
                    if old_state:
                        if hooks_changed:
                            old_state["hooks"] = hooks.register_targets(old_state.get("hooks", []), layout.app / "hook_capture.py", layout.data,
                                                                       old_state["python"], old_state["install_id"], layout.system, layout.config / "hook-backups")
                        old_state["startup"] = {}
                        if old_startup:
                            services.register_startup(old_state, old_startup, activate=was_running)
                        elif was_running:
                            services.start(old_state)
                        write_json(layout.state_file, old_state)
                    else:
                        state.update(status="failed", startup={}, hooks=[])
                        write_json(layout.state_file, state)
                except Exception as rollback_error:
                    rollback_errors.append("restore: " + str(rollback_error))
            if rollback_errors:
                raise RuntimeError(str(error) + " (rollback needs repair: " + "; ".join(rollback_errors) + ")") from error
            if not replaced and backup.exists():
                backup.rename(layout.app)
                if was_running:
                    services.start(previous)
            raise
        finally:
            if stage.exists():
                shutil.rmtree(stage)


def start_installation(layout: Layout) -> dict:
    with installation_lock(layout):
        state = load_state(layout, required=True)
        if not layout.app.is_dir() or state["status"] == "uninstalled":
            raise RuntimeError("Recall code was removed; run the installer again")
        managed_app(layout, state)
        verify_package(layout.app)
        probe_python(state["python"])
        if not owned_service(state):
            selected = free_port(state["port"])
            if selected != state["port"]:
                services.stop(state)
                state["port"] = selected
                if state.get("startup"):
                    descriptor = services.startup_descriptor(layout, state, state["startup"]["kind"])
                    services.register_startup(state, descriptor)
            services.start(state)
        write_json(layout.state_file, state)
        return {"started": True, "url": "http://127.0.0.1:" + str(state["port"])}


def stop_installation(layout: Layout) -> dict:
    with installation_lock(layout):
        state = load_state(layout, required=True)
        services.stop(state)
        return {"stopped": not bool(owned_service(state)), "autostart_retained": bool(state.get("startup"))}


def uninstall(layout: Layout) -> dict:
    with installation_lock(layout):
        state = load_state(layout, required=True)
        if layout.app.exists():
            managed_app(layout, state)
        leave_code_directory(layout)
        services.unregister_startup(state)
        removed = hooks.remove_targets(state.get("hooks", []), layout.app / "hook_capture.py", state["install_id"])
        write_json(layout.data / "trae-hooks-installed.json", {"install_id": state["install_id"], "configs": []})
        if layout.app.exists():
            deadline = time.monotonic() + 5
            while True:
                try:
                    shutil.rmtree(layout.app)
                    break
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.1)
        state.update(status="uninstalled", startup={}, hooks=[], uninstalled_at=time.time())
        write_json(layout.state_file, state)
        return {"uninstalled": True, "data_retained": True, "data_dir": str(layout.data), "hooks": removed}
