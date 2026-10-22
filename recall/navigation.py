"""Open only stored conversation identities, through native apps or a terminal."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import plistlib
import re
import shlex
import shutil
import subprocess
import threading
import time
import urllib.parse
import uuid
from pathlib import Path

WEB_HOSTS = {
    "chatgpt": {"chatgpt.com", "chat.openai.com"},
    "claude": {"claude.ai"},
    "trae": {"trae.ai", "www.trae.ai", "trae.cn", "www.trae.cn"},
    "gemini": {"gemini.google.com"},
    "doubao": {"doubao.com", "www.doubao.com"},
}


def conversation_id(value) -> str | None:
    try:
        identifier = str(uuid.UUID(str(value)))
        return identifier if identifier == str(value).lower() else None
    except (ValueError, TypeError, AttributeError):
        return None


def safe_web_url(provider: str, value: str) -> str | None:
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) <= 32 for c in value) or "\\" in value:
        return None
    try:
        url = urllib.parse.urlsplit(value)
        if url.scheme != "https" or url.username or url.password or url.port not in {None, 443}:
            return None
        if url.hostname not in WEB_HOSTS.get(provider, set()) or url.query or url.fragment:
            return None
        segments = url.path.rstrip("/").split("/")
        if provider == "chatgpt" and len(segments) == 3 and segments[1] in {"c", "share"} and conversation_id(segments[2]):
            return value
        if provider == "claude" and len(segments) == 3 and segments[1] == "chat" and conversation_id(segments[2]):
            return value
        prefixes = {"trae": {"share", "chat"}, "gemini": {"app"}, "doubao": {"chat"}}
        if provider in prefixes and len(segments) == 3 and segments[1] in prefixes[provider] and re.fullmatch(
                r"[a-zA-Z0-9_-]{8,200}", segments[2]):
            return value
    except ValueError:
        pass
    return None


def macos_codex_app(home: Path) -> Path | None:
    for directory in [home / "Applications", Path("/Applications")]:
        for name in ["Codex.app", "ChatGPT.app"]:
            app = directory / name
            try:
                info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
                schemes = {scheme for entry in info.get("CFBundleURLTypes", []) for scheme in entry.get("CFBundleURLSchemes", [])}
                if info.get("CFBundleIdentifier") == "com.openai.codex" and "codex" in schemes:
                    return app
            except (OSError, ValueError, plistlib.InvalidFileException):
                continue
    return None


def executable(name: str, home: Path) -> str | None:
    resolved = shutil.which(name) if home == Path.home().resolve() else None
    if resolved:
        return resolved
    roots = [home / ".local" / "bin", home / ".local" / "node" / "bin", home / ".npm-global" / "bin",
             Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
    if os.name == "nt":
        roots += [home / "AppData" / "Roaming" / "npm", home / ".local" / "bin"]
    for root in roots:
        for suffix in [".exe", ".cmd", ""] if os.name == "nt" else [""]:
            candidate = root / (name + suffix)
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None


def discover_targets(home: Path, system: str) -> dict:
    result = {"codex_cli": executable("codex", home), "claude_cli": executable("claude", home)}
    if system == "Darwin":
        app = macos_codex_app(home)
        result["codex_app"] = str(app) if app else None
        result["terminal"] = "macos-terminal" if Path("/System/Applications/Utilities/Terminal.app").is_dir() else None
    elif system == "Windows":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "codex") as key:
                winreg.QueryValueEx(key, "URL Protocol")
            result["codex_app"] = "windows-protocol"
        except (OSError, ImportError):
            result["codex_app"] = None
        result["terminal"] = shutil.which("powershell.exe")
    elif system == "Linux":
        result["codex_app"] = None
        if shutil.which("xdg-mime") and shutil.which("xdg-open"):
            try:
                result["codex_app"] = "linux-protocol" if subprocess.run(
                    ["xdg-mime", "query", "default", "x-scheme-handler/codex"], capture_output=True, text=True, timeout=3).stdout.strip() else None
            except (OSError, subprocess.TimeoutExpired):
                pass
        result["terminal"] = next((shutil.which(name) for name in ["x-terminal-emulator", "gnome-terminal", "konsole", "xterm"] if shutil.which(name)), None)
    return result


class NavigationError(ValueError):
    pass


class Navigator:
    def __init__(self, store, roots: dict, system: str | None = None, home: Path | None = None, targets: dict | None = None):
        self.store = store
        self.roots = roots
        self.system = system or platform.system()
        self.home = (home or Path.home()).resolve()
        self._targets = targets
        self._detected = {}
        self._detected_at = None
        self._lock = threading.Lock()
        self._recent = {}

    def targets(self):
        if self._targets is not None:
            return self._targets
        if self._detected_at is None or time.monotonic() - self._detected_at > 30:
            self._detected = discover_targets(self.home, self.system)
            self._detected_at = time.monotonic()
        return self._detected

    def paths(self, keys: list[str]) -> dict:
        if not keys:
            return {}
        con = self.store.connect()
        try:
            result = {key: [] for key in keys}
            placeholders = ",".join("?" for _ in keys)
            for row in con.execute("SELECT session_key,path FROM source_files WHERE session_key IN (" + placeholders + ")", keys):
                result[row["session_key"]].append(row["path"])
            return result
        finally:
            con.close()

    def local_paths(self, provider: str, paths: list[str]) -> list[Path]:
        root = self.roots.get(provider)
        if not root:
            return []
        base = Path(root).expanduser().resolve()
        result = []
        for value in paths:
            path = Path(value).expanduser().resolve()
            if path.is_file() and path.is_relative_to(base):
                result.append(path)
        return result

    def describe(self, session: dict, paths: list[str] | None = None) -> dict:
        targets = self.targets()
        provider, identity = session["provider"], conversation_id(session.get("native_id"))
        paths = paths if paths is not None else self.paths([session["key"]])[session["key"]]
        available = self.local_paths(provider, paths)
        if identity and session.get("source_kind") == "local" and available:
            if provider == "codex" and targets.get("codex_app"):
                return {"available": True, "kind": "codex", "label": "打开原对话", "destination": "Codex", "native_id": identity,
                        "url": "codex://threads/" + identity, "mirrored": False}
            if provider in {"codex", "claude"} and targets.get(provider + "_cli") and targets.get("terminal"):
                if provider == "claude" and not any(path.stem.lower() == identity for path in available):
                    return self.unavailable("原始 Claude Code 记录已移动，可以查看看板中保存的原文。")
                cwd = session.get("cwd", "")
                if not cwd or any(ord(c) < 32 for c in cwd) or not Path(cwd).is_dir():
                    return self.unavailable("原项目目录已移动，无法在原目录恢复终端会话；仍可查看原文。")
                return {"available": True, "kind": "terminal", "label": "打开原对话", "destination": "Claude Code" if provider == "claude" else "Codex CLI",
                        "provider": provider, "native_id": identity, "cwd": str(Path(cwd).resolve()), "mirrored": False}
        mirror = conversation_id(session.get("mirror_thread_id"))
        if provider == "claude" and mirror and self.local_paths("codex", paths) and targets.get("codex_app"):
            return {"available": True, "kind": "codex", "label": "打开 Codex 镜像", "destination": "Codex 镜像", "native_id": mirror,
                    "url": "codex://threads/" + mirror, "mirrored": True}
        url = safe_web_url(provider, session.get("original_url", ""))
        if not url and provider == "chatgpt" and identity:
            url = "https://chatgpt.com/c/" + identity
        if url:
            return {"available": True, "kind": "web", "label": "打开原对话", "destination": "原对话网页", "url": url, "mirrored": False}
        if provider == "trae":
            return self.unavailable("Trae 当前未提供已验证的本地会话跳转入口；可查看原文，或按会话标题回到 Trae 查找。")
        if provider in {"codex", "claude"} and session.get("source_kind") == "local":
            return self.unavailable("原始记录或对应客户端不可用；可以查看看板中保存的原文。")
        return self.unavailable("这段导入记录没有可打开的原对话链接；可以查看已收录的原文。")

    @staticmethod
    def unavailable(reason):
        return {"available": False, "label": "查看原文", "reason": reason}

    def decorate(self, sessions: list[dict]):
        paths = self.paths([session["key"] for session in sessions])
        for session in sessions:
            session["navigation"] = self.describe(session, paths[session["key"]])
        return sessions

    def verify_native(self, target: dict, session: dict, paths: list[str]):
        if target["kind"] not in {"codex", "terminal"}:
            return
        identity = target["native_id"]
        provider = "codex" if target["kind"] == "codex" else target["provider"]
        candidates = self.local_paths(provider, paths)
        if provider == "claude":
            for path in candidates:
                if path.stem.lower() != identity:
                    continue
                try:
                    with path.open("rb") as source:
                        for _ in range(30):
                            line = source.readline(1024 * 1024)
                            if not line:
                                break
                            row = json.loads(line)
                            original = row.get("sessionId") or row.get("session_id")
                            if original:
                                if conversation_id(original) == identity:
                                    return
                                break
                except (OSError, ValueError, AttributeError):
                    continue
        else:
            for path in candidates:
                try:
                    with path.open("rb") as source:
                        for _ in range(5):
                            row = json.loads(source.readline(1024 * 1024))
                            payload = row.get("payload") or {}
                            if row.get("type") == "session_meta" and conversation_id(payload.get("id") or payload.get("session_id")) == identity:
                                return
                except (OSError, ValueError, AttributeError):
                    continue
        raise NavigationError("原始会话记录已移动或身份发生变化，请重新同步后再打开。")

    def open(self, key: str) -> dict:
        detail = self.store.detail(key, limit=1)
        if not detail:
            raise KeyError("找不到这个会话。")
        session = detail["session"]
        target = self.describe(session, session["source_paths"])
        if not target["available"]:
            raise NavigationError(target["reason"])
        self.verify_native(target, session, session["source_paths"])
        with self._lock:
            now = time.monotonic()
            previous = self._recent.get(key)
            if previous is not None and now - previous < 2:
                return {"opened": True, "already_requested": True, "destination": target["destination"], "mirrored": target["mirrored"]}
            try:
                self.launch(target)
            except (OSError, subprocess.SubprocessError) as error:
                raise NavigationError("客户端未能打开，请检查本机应用或终端是否可用。") from error
            self._recent = {identifier: when for identifier, when in self._recent.items() if now - when < 10}
            self._recent[key] = now
        return {"opened": True, "destination": target["destination"], "mirrored": target["mirrored"],
                "kind": target["kind"]}

    @staticmethod
    def run(command: list[str]):
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=8)
        if result.returncode:
            raise NavigationError("系统无法打开对应应用，请确认客户端已安装。")

    def launch(self, target: dict):
        if target["kind"] in {"codex", "web"}:
            url = target["url"]
            if self.system == "Darwin":
                command = ["/usr/bin/open", "-a", self.targets()["codex_app"], url] if target["kind"] == "codex" else ["/usr/bin/open", url]
                self.run(command)
            elif self.system == "Windows":
                os.startfile(url)
            else:
                self.run(["xdg-open", url])
            return
        targets = self.targets()
        binary = targets[target["provider"] + "_cli"]
        command = [binary, "--resume" if target["provider"] == "claude" else "resume", target["native_id"]]
        if self.system == "Darwin":
            directory = self.store.directory / "launch"
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(0o700)
            identifier = hashlib.sha256((target["provider"] + target["native_id"]).encode()).hexdigest()[:24]
            script = directory / ("recall-" + identifier + ".command")
            search_path = ":".join(dict.fromkeys([str(Path(binary).parent), str(self.home / ".local" / "node" / "bin"),
                                                 "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]))
            body = "#!/bin/sh\nset -eu\nexport PATH=" + shlex.quote(search_path) + ':"$PATH"\ncd -- ' + shlex.quote(target["cwd"]) + "\nexec " + shlex.join(command) + "\n"
            temporary = script.with_suffix(".tmp")
            temporary.write_text(body, encoding="utf-8")
            temporary.chmod(0o700)
            os.replace(temporary, script)
            self.run(["/usr/bin/open", "-b", "com.apple.Terminal", str(script)])
        elif self.system == "Windows":
            quote = lambda value: "'" + value.replace("'", "''") + "'"
            body = "Set-Location -LiteralPath " + quote(target["cwd"]) + "; & " + " ".join(quote(arg) for arg in command)
            encoded = base64.b64encode(body.encode("utf-16-le")).decode("ascii")
            subprocess.Popen([targets["terminal"], "-NoProfile", "-NoExit", "-EncodedCommand", encoded],
                             cwd=target["cwd"], creationflags=subprocess.CREATE_NEW_CONSOLE, close_fds=True)
        else:
            terminal = targets["terminal"]
            arguments = [terminal, "--"] + command if Path(terminal).name == "gnome-terminal" else [terminal, "-e"] + command
            subprocess.Popen(arguments, cwd=target["cwd"], start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
