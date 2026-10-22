"""Agent and human entry point; every result can be emitted as structured JSON."""
from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path

from . import __version__
from .doctor import doctor
from .install import PACKAGE_ROOT, install, load_state, plan_install, start_installation, stop_installation, uninstall
from .paths import Layout


def default_prefix():
    marker = PACKAGE_ROOT / ".installation-owner.json"
    return PACKAGE_ROOT.parent if PACKAGE_ROOT.name == "app" and marker.is_file() else None


def parser():
    root = argparse.ArgumentParser(description="Recall: install and manage a local AI conversation dashboard")
    root.add_argument("--version", action="version", version="Recall " + __version__)
    commands = root.add_subparsers(dest="command", required=True)
    for name in ["install", "doctor", "start", "stop", "uninstall", "serve"]:
        command = commands.add_parser(name)
        command.add_argument("--prefix", type=Path, default=default_prefix(), help="Dedicated per-user installation directory")
        command.add_argument("--home", type=Path, help="Source/launch-item home override for isolated tests")
        command.add_argument("--json", action="store_true", help="Machine-readable result without HTTP tokens")
        if name == "install":
            command.add_argument("--source", type=Path, default=PACKAGE_ROOT, help="Verified extracted distribution folder")
            command.add_argument("--python", help="Python 3.9+ executable with SQLite FTS5")
            command.add_argument("--port", type=int, help="Preferred localhost port; occupied ports are skipped")
            command.add_argument("--interval", type=int, default=30)
            command.add_argument("--plan", action="store_true", help="Read-only installation preview")
            command.add_argument("--no-autostart", action="store_true")
            command.add_argument("--no-hooks", action="store_true")
            command.add_argument("--no-open", action="store_true")
        if name == "doctor":
            command.add_argument("--no-smoke", action="store_true")
        if name == "start":
            command.add_argument("--open", action="store_true")
    return root


def resolve_layout(args):
    prefix = args.prefix
    home = args.home
    # A stable installed entry point also works with the explicit test home.
    if prefix and not home:
        registry = prefix.expanduser() / "config" / "installation.json"
        if registry.is_file() and not registry.is_symlink():
            try:
                value = json.loads(registry.read_text(encoding="utf-8"))
                if value.get("app") == "recall":
                    home = Path(value["home"])
            except (OSError, ValueError, KeyError, TypeError):
                pass
    return Layout.resolve(home, prefix)


def emit(result: dict, as_json: bool):
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if result.get("installed"):
        print("Recall " + result["version"] + " installed: " + result["url"])
        print("Control: " + result["control"])
        print("Data: " + str(Path(result["prefix"]) / "data"))
        for warning in result["doctor"]["warnings"]:
            print("Pending: " + warning)
    elif "ready" in result:
        print("Recall doctor: " + ("READY" if result["ready"] else "NEEDS REPAIR"))
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        layout = resolve_layout(args)
        if args.command == "install":
            options = {key: getattr(args, key) for key in ["source", "python", "port", "interval", "no_autostart", "no_hooks"]}
            if args.plan:
                planned = plan_install(layout, **options)
                result = {"plan": True, "mutations": False, "version": planned["manifest"]["version"],
                          "prefix": str(layout.base), "url": planned["url"], "runtime": planned["runtime"],
                          "sources": planned["detection"], "hooks": planned["targets"],
                          "autostart": {key: planned["startup"][key] for key in ["kind", "path", "label"]} if planned["startup"] else {"kind": "manual"},
                          "data_retained": True}
            else:
                result = install(layout, **options)
                if not args.no_open:
                    webbrowser.open(result["url"])
        elif args.command == "doctor":
            result = doctor(layout, smoke=not args.no_smoke)
        elif args.command == "start":
            result = start_installation(layout)
            if args.open:
                webbrowser.open(result["url"])
        elif args.command == "stop":
            result = stop_installation(layout)
        elif args.command == "uninstall":
            result = uninstall(layout)
        else:
            from .server import main as serve
            state = load_state(layout, required=True)
            serve(["--data-dir", state["data_dir"], "--config", state["roots_file"], "--port", str(state["port"]),
                   "--interval", str(state["interval"]), "--instance", state["install_id"]])
            return 0
        emit(result, args.json)
        return 0 if result.get("ready", True) else 1
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        emit({"ok": False, "error": str(error), "command": args.command}, args.json)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
