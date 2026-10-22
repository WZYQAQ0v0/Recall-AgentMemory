"""Resolve the recipient's paths without assuming a username or workspace."""
from __future__ import annotations

import hashlib
import os
import platform
from dataclasses import dataclass
from pathlib import Path


def actual_home(home: Path) -> bool:
    return home.resolve() == Path.home().resolve()


def user_directory(home: Path, env_key: str, fallback: Path) -> Path:
    value = os.environ.get(env_key) if actual_home(home) else None
    return Path(value).expanduser().resolve() if value else fallback


@dataclass(frozen=True)
class Layout:
    home: Path
    base: Path
    system: str

    @classmethod
    def resolve(cls, home: Path | None = None, prefix: Path | None = None, system: str | None = None):
        home = (home or Path.home()).expanduser().resolve()
        system = system or platform.system()
        if prefix:
            base = prefix.expanduser().resolve()
        elif system == "Darwin":
            base = home / "Library" / "Application Support" / "Recall"
        elif system == "Windows":
            base = user_directory(home, "LOCALAPPDATA", home / "AppData" / "Local") / "Recall"
        elif system == "Linux":
            base = user_directory(home, "XDG_DATA_HOME", home / ".local" / "share") / "recall"
        else:
            raise ValueError("Unsupported system: " + system)
        return cls(home, base, system)

    @property
    def app(self):
        return self.base / "app"

    @property
    def data(self):
        return self.base / "data"

    @property
    def config(self):
        return self.base / "config"

    @property
    def roots_file(self):
        return self.config / "sources.json"

    @property
    def state_file(self):
        return self.config / "installation.json"

    @property
    def logs(self):
        return self.base / "logs"

    @property
    def service_label(self):
        suffix = hashlib.sha256(str(self.base).encode()).hexdigest()[:10]
        return "local.recall.session-atlas." + suffix

    @property
    def config_home(self):
        return user_directory(self.home, "XDG_CONFIG_HOME", self.home / ".config")


def detect_sources(layout: Layout) -> dict:
    home = layout.home
    codex = user_directory(home, "CODEX_HOME", home / ".codex")
    claude = user_directory(home, "CLAUDE_CONFIG_DIR", home / ".claude") / "projects"
    if layout.system == "Darwin":
        app_data = home / "Library" / "Application Support"
    elif layout.system == "Windows":
        app_data = user_directory(home, "APPDATA", home / "AppData" / "Roaming")
    else:
        app_data = layout.config_home
    clients = []
    for edition, folder, settings in [("Trae CN", "Trae CN", ".trae-cn"), ("Trae", "Trae", ".trae")]:
        data_path = app_data / folder
        config_path = home / settings / "hooks.json"
        detected = data_path.is_dir() or config_path.parent.is_dir()
        if layout.system == "Darwin" and actual_home(home):
            detected = detected or any((p / (folder + ".app")).exists() for p in [Path("/Applications"), home / "Applications"])
        if detected:
            clients.append({"client": edition, "data_path": str(data_path), "config_path": str(config_path)})
    trae = next((c["data_path"] for c in clients), str(app_data / "Trae CN"))
    roots = {"codex": str(codex), "claude": str(claude), "trae": trae}
    return {"roots": roots, "trae_clients": clients, "detected": {"codex": codex.is_dir(), "claude": claude.is_dir(), "trae": bool(clients)}}
