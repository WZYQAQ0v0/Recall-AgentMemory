"""Per-user startup adapters; never create system-wide/root services."""
from __future__ import annotations

import base64
import hashlib
import os
import plistlib
import shutil
import subprocess
import time
from pathlib import Path

from .paths import Layout, user_directory
from .portable import atomic_text, private_directory
from .runtime import owned_service, shutdown_owned, wait_ready

CHILDREN = {}


def server_command(state: dict, background: bool = False) -> list[str]:
    command = [state["python"], "-B", str(Path(state["app_dir"]) / "app.py"), "--data-dir", state["data_dir"],
               "--config", state["roots_file"], "--port", str(state["port"]), "--instance", state["install_id"],
               "--interval", str(state.get("interval", 30))]
    if background:
        command.append("--background")
    return command


def run(command: list[str], check: bool = True):
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
    if check and result.returncode:
        raise RuntimeError("Startup command failed: " + " ".join(command[:3]) + ": " + result.stderr.strip()[:500])
    return result


def desktop_command(arguments: list[str]) -> str:
    # Exec has two decoding passes: desktop-entry escapes, then argument quoting.
    def quote(arg):
        quoted = arg.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace(chr(96), "\\" + chr(96))
        return '"' + quoted.replace("\\", "\\\\").replace("%", "%%") + '"'
    return " ".join(quote(arg) for arg in arguments)


def startup_descriptor(layout: Layout, state: dict, kind: str | None = None) -> dict:
    label = state.get("service_label") or layout.service_label
    command = server_command(state)
    if layout.system == "Darwin":
        path = layout.home / "Library" / "LaunchAgents" / (label + ".plist")
        value = {"Label": label, "ProgramArguments": command, "WorkingDirectory": state["app_dir"], "RunAtLoad": True,
                 "KeepAlive": True, "ThrottleInterval": 10, "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"},
                 "StandardOutPath": str(Path(state["log_dir"]) / "service.log"), "StandardErrorPath": str(Path(state["log_dir"]) / "service-error.log")}
        return {"kind": "launchd", "path": str(path), "label": label, "content": plistlib.dumps(value).decode("utf-8")}
    if layout.system == "Linux":
        if kind is None:
            kind = "systemd" if shutil.which("systemctl") and run(["systemctl", "--user", "show-environment"], check=False).returncode == 0 else "xdg-autostart"
        if kind == "systemd":
            path = layout.config_home / "systemd" / "user" / (label + ".service")
            quote = lambda arg: '"' + arg.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'
            body = "[Unit]\nDescription=Recall local conversation index\n[Service]\nType=simple\nExecStart=" + " ".join(quote(arg) for arg in command) + "\nRestart=on-failure\nRestartSec=5\nEnvironment=PYTHONUNBUFFERED=1\n[Install]\nWantedBy=default.target\n"
            return {"kind": kind, "path": str(path), "label": label + ".service", "content": body}
        path = layout.config_home / "autostart" / (label + ".desktop")
        return {"kind": "xdg-autostart", "path": str(path), "label": label,
                "content": "[Desktop Entry]\nType=Application\nName=Recall\nExec=" + desktop_command(server_command(state, background=True)) + "\nTerminal=false\nX-GNOME-Autostart-enabled=true\n"}
    if layout.system == "Windows":
        roaming = user_directory(layout.home, "APPDATA", layout.home / "AppData" / "Roaming")
        path = roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / (label + ".vbs")
        arguments = server_command(state, background=True)
        pythonw = Path(state["python"]).with_name("pythonw.exe")
        if pythonw.is_file():
            arguments[0] = str(pythonw)
        quote = lambda arg: "'" + arg.replace("'", "''") + "'"
        # Encoded arguments keep Unicode and literal % paths out of WScript's
        # environment expansion. No user/machine execution policy is changed.
        script = "& " + " ".join(quote(arg) for arg in arguments)
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        return {"kind": "windows-startup", "path": str(path), "label": label,
                "content": "' Recall installation " + state["install_id"] + "\nSet shell = CreateObject(\"WScript.Shell\")\nshell.Run \"powershell.exe -NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand " + encoded + "\", 0, False\n"}
    raise ValueError("Unsupported system")


def startup_owned(startup: dict) -> bool:
    if not startup:
        return True
    path = Path(startup["path"])
    return path.is_file() and not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == startup.get("sha256")


def check_registration(state: dict, descriptor: dict):
    path = Path(descriptor["path"])
    known = state.get("startup", {})
    if path.exists() or path.is_symlink():
        if known.get("path") != str(path) or not startup_owned(known):
            raise RuntimeError("An unrelated or edited startup file exists; kept: " + str(path))
    if descriptor["kind"] == "launchd":
        label = "gui/" + str(os.getuid()) + "/" + descriptor["label"]
        if run(["launchctl", "print", label], check=False).returncode == 0 and not known:
            raise RuntimeError("An unrelated launchd job has this label; choose another installation prefix")


def register_startup(state: dict, descriptor: dict, activate: bool = True):
    check_registration(state, descriptor)
    path = Path(descriptor["path"])
    if descriptor["kind"] == "windows-startup":
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = descriptor["content"].encode("utf-16")
        temporary = path.with_suffix(".tmp")
        try:
            temporary.write_bytes(payload)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    else:
        atomic_text(path, descriptor["content"])
    state["startup"] = {key: descriptor[key] for key in ["kind", "path", "label"]}
    state["startup"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    if activate:
        start(state)


def start(state: dict):
    if owned_service(state):
        return
    startup = state.get("startup", {})
    if startup and not startup_owned(startup):
        raise RuntimeError("The managed startup file is missing or edited; reinstall to repair it")
    kind = startup.get("kind")
    if kind == "launchd":
        domain = "gui/" + str(os.getuid())
        label = domain + "/" + startup["label"]
        if run(["launchctl", "print", label], check=False).returncode:
            run(["launchctl", "bootstrap", domain, startup["path"]])
        run(["launchctl", "enable", label])
        run(["launchctl", "kickstart", "-k", label])
    elif kind == "systemd":
        run(["systemctl", "--user", "daemon-reload"])
        run(["systemctl", "--user", "enable", "--now", startup["label"]])
    else:
        private_directory(Path(state["log_dir"]))
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with (Path(state["log_dir"]) / "service.log").open("ab") as log:
            process = subprocess.Popen(server_command(state), cwd=state["app_dir"], stdout=log, stderr=log,
                                       start_new_session=os.name != "nt", creationflags=flags)
        state["pid"] = process.pid
        CHILDREN[state["install_id"]] = process
    wait_ready(state)


def stop(state: dict):
    startup = state.get("startup", {})
    if startup and not startup_owned(startup):
        raise RuntimeError("The startup file belongs to another owner or was edited; no job was stopped")
    if startup.get("kind") == "launchd":
        label = "gui/" + str(os.getuid()) + "/" + startup["label"]
        result = run(["launchctl", "bootout", label], check=False)
        if result.returncode not in {0, 3, 113}:
            raise RuntimeError("LaunchAgent could not be unloaded: " + result.stderr.strip()[:300])
        deadline = time.monotonic() + 5
        while run(["launchctl", "print", label], check=False).returncode == 0:
            if time.monotonic() >= deadline:
                raise RuntimeError("LaunchAgent is still unloading; retry stop")
            time.sleep(0.1)
    elif startup.get("kind") == "systemd":
        run(["systemctl", "--user", "stop", startup["label"]], check=False)
    shutdown_owned(state)
    process = CHILDREN.get(state["install_id"])
    if process and process.args == server_command(state):
        if process.poll() is None:
            # A child we launched can fail before it exposes the HTTP endpoint.
            # Its exact argument array proves ownership even in that case.
            process.terminate()
        process.wait(timeout=5)
        CHILDREN.pop(state["install_id"], None)


def unregister_startup(state: dict):
    startup = state.get("startup", {})
    stop(state)
    if startup.get("kind") == "systemd":
        run(["systemctl", "--user", "disable", startup["label"]], check=False)
    if startup:
        Path(startup["path"]).unlink(missing_ok=True)
    state["startup"] = {}


def startup_health(state: dict) -> dict:
    startup = state.get("startup", {})
    if not startup:
        return {"enabled": False, "method": "manual"}
    result = {"enabled": startup_owned(startup), "method": startup["kind"], "path": startup["path"]}
    if startup["kind"] == "launchd":
        result["loaded"] = run(["launchctl", "print", "gui/" + str(os.getuid()) + "/" + startup["label"]], check=False).returncode == 0
    elif startup["kind"] == "systemd":
        result["loaded"] = run(["systemctl", "--user", "is-active", startup["label"]], check=False).returncode == 0
    return result
