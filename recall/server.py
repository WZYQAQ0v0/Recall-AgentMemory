#!/usr/bin/env python3
"""Run Recall on localhost. Python standard library only; no remote services."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .core import Indexer, Store, normalize_import, save_imports
from .paths import Layout
from .runtime import LOCAL_HTTP
from .navigation import Navigator, NavigationError
from . import __version__

APP_DIR = Path(__file__).resolve().parent
VERSION = __version__


def default_data_dir() -> Path:
    return Layout.resolve().data


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple, store: Store, indexer: Indexer, instance: str = "development"):
        super().__init__(address, Handler)
        self.store = store
        self.indexer = indexer
        self.navigator = Navigator(store, indexer.roots)
        self.instance = instance
        self.token = secrets.token_urlsafe(32)
        self.stopping = threading.Event()
        self.origins = {f"http://127.0.0.1:{address[1]}", f"http://localhost:{address[1]}"}


class Handler(BaseHTTPRequestHandler):
    server: Server

    def log_message(self, *args):
        # Search terms and private session titles should not be copied to HTTP logs.
        pass

    def allowed_host(self) -> bool:
        host = self.headers.get("Host", "")
        port = self.server.server_address[1]
        return host in {f"127.0.0.1:{port}", f"localhost:{port}"} and self.headers.get("Sec-Fetch-Site", "none") not in {"cross-site", "same-site"}

    def respond(self, status: int, payload, content_type: str = "application/json; charset=utf-8"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        if not self.allowed_host():
            return self.respond(403, {"error": "请从本机看板地址访问。"})
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        get = lambda key, default="": params.get(key, [default])[0]
        try:
            if parsed.path == "/api/bootstrap":
                return self.respond(200, {"app": "session-atlas", "version": VERSION, "token": self.server.token,
                                          "data_dir": str(self.server.store.directory), "app_dir": str(APP_DIR.parent),
                                          "instance": self.server.instance, "pid": os.getpid()})
            if parsed.path == "/api/status":
                return self.respond(200, {"stats": self.server.store.stats(), "index": self.server.indexer.status()})
            if parsed.path == "/api/search":
                result = self.server.store.search(query=get("q"), provider=get("provider"), project=get("project"),
                    favorite=get("favorite") == "1", days=min(36500, max(0, int(get("days", "0")))), sort=get("sort", "recent"),
                    offset=max(0, int(get("offset", "0"))), limit=min(60, max(1, int(get("limit", "24")))))
                self.server.navigator.decorate(result["sessions"])
                return self.respond(200, result)
            if parsed.path == "/api/session":
                result = self.server.store.detail(get("id"), query=get("q"), focus=get("focus"),
                    offset=int(get("offset")) if get("offset") else None,
                    limit=min(60, max(1, int(get("limit", "24")))))
                if result:
                    result["session"]["navigation"] = self.server.navigator.describe(result["session"], result["session"]["source_paths"])
                return self.respond(200, result) if result else self.respond(404, {"error": "找不到这个会话。"})
            static = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "application/javascript; charset=utf-8"), "/styles.css": ("styles.css", "text/css; charset=utf-8"),
                      "/favicon.svg": ("favicon.svg", "image/svg+xml")}
            if parsed.path in static:
                filename, kind = static[parsed.path]
                return self.respond(200, (APP_DIR / "web" / filename).read_bytes(), kind)
            return self.respond(404, {"error": "页面不存在。"})
        except (ValueError, TypeError):
            return self.respond(400, {"error": "请求参数格式不正确。"})
        except Exception as error:
            print("request error:", type(error).__name__, flush=True)
            return self.respond(500, {"error": "读取索引时出错，请稍后重试。"})

    def do_POST(self):
        if not self.allowed_host() or self.headers.get("Origin") not in self.server.origins | {None}:
            return self.respond(403, {"error": "请从本机看板操作。"})
        if not secrets.compare_digest(self.headers.get("X-Atlas-Token", ""), self.server.token):
            return self.respond(403, {"error": "页面已过期，请刷新后重试。"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0:
                return self.respond(400, {"error": "请求长度格式不正确。"})
            if length > 32 * 1024 * 1024:
                return self.respond(413, {"error": "单个导入文件上限为 24 MB，请拆分后导入。"})
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                return self.respond(400, {"error": "请求需要包含一个 JSON 对象。"})
            path = urllib.parse.urlparse(self.path).path
            if path == "/api/open-session":
                if set(data) != {"id"} or not isinstance(data["id"], str):
                    return self.respond(400, {"error": "打开会话只接受看板中的会话标识。"})
                try:
                    return self.respond(200, self.server.navigator.open(data["id"]))
                except KeyError:
                    return self.respond(404, {"error": "找不到这个会话。"})
                except NavigationError as error:
                    return self.respond(409, {"error": str(error)})
            if path == "/api/sync":
                if not self.server.indexer.status()["running"]:
                    threading.Thread(target=self.server.indexer.scan, daemon=True).start()
                return self.respond(202, {"accepted": True})
            if path == "/api/annotate":
                favorite = data.get("favorite") if isinstance(data.get("favorite"), bool) else None
                notes = data.get("notes") if isinstance(data.get("notes"), str) else None
                okay = self.server.store.annotate(str(data.get("id", "")), favorite=favorite, notes=notes)
                return self.respond(200, {"saved": True}) if okay else self.respond(404, {"error": "找不到这个会话。"})
            if path == "/api/import":
                content = data.get("content", "")
                if not isinstance(content, str) or len(content.encode()) > 24 * 1024 * 1024:
                    return self.respond(413, {"error": "请选择小于 24 MB 的导出文件。"})
                sessions = normalize_import(content, str(data.get("provider") or "other"), str(data.get("filename") or "导入会话.json"))
                save_imports(self.server.store, sessions)
                threading.Thread(target=self.server.indexer.scan, daemon=True).start()
                return self.respond(200, {"imported": len(sessions), "message_count": sum(len(s["messages"]) for s in sessions)})
            if path == "/api/reveal":
                result = self.server.store.detail(str(data.get("id", "")), limit=1)
                candidates = result["session"]["source_paths"] if result else []
                source = next((p for p in candidates if Path(p).is_file()), None)
                if not source:
                    return self.respond(404, {"error": "原始文件已移动，索引中的对话仍可查看。"})
                if sys.platform == "darwin":
                    subprocess.Popen(["open", "-R", source], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif sys.platform == "win32":
                    subprocess.Popen(["explorer.exe", "/select," + source], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                elif sys.platform.startswith("linux"):
                    subprocess.Popen(["xdg-open", str(Path(source).parent)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return self.respond(200, {"path": source})
            if path == "/api/shutdown":
                self.respond(200, {"stopped": True})
                self.server.stopping.set()
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            return self.respond(404, {"error": "操作不存在。"})
        except (ValueError, TypeError) as error:
            return self.respond(400, {"error": str(error) if isinstance(error, ValueError) else "输入格式不正确。"})
        except Exception as error:
            print("write error:", type(error).__name__, flush=True)
            return self.respond(500, {"error": "保存时出错，请稍后重试。"})


def bootstrap(port: int) -> dict | None:
    try:
        with LOCAL_HTTP.open(f"http://127.0.0.1:{port}/api/bootstrap", timeout=1) as response:
            result = json.load(response)
            return result if result.get("app") == "session-atlas" else None
    except (OSError, urllib.error.URLError, ValueError):
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Recall · 本机会话索引")
    parser.add_argument("--data-dir", default=str(default_data_dir()))
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--config", help="包含 codex / claude / trae 数据目录的 JSON 文件")
    parser.add_argument("--interval", type=int, default=30)
    parser.add_argument("--instance", default="development")
    parser.add_argument("--scan", action="store_true", help="读取一次历史记录后退出")
    parser.add_argument("--import-file", help="导入 JSON / JSONL / TXT 导出文件")
    parser.add_argument("--provider", default="other")
    parser.add_argument("--background", action="store_true", help="在后台运行服务")
    parser.add_argument("--open", action="store_true", help="打开浏览器")
    parser.add_argument("--stop", action="store_true", help="停止该端口上的 Recall 服务")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("端口需要在 1024 到 65535 之间。")
    directory = Path(args.data_dir).expanduser().resolve()
    url = f"http://127.0.0.1:{args.port}"
    existing = bootstrap(args.port) if not args.scan and not args.import_file else None
    if args.stop:
        if not existing:
            print("Recall 服务没有运行。")
            return
        if Path(existing["data_dir"]).resolve() != directory or existing.get("instance") != args.instance:
            raise SystemExit("这个端口属于另一份索引，未停止其服务。")
        request = urllib.request.Request(url + "/api/shutdown", data=b"{}", headers={"Content-Type": "application/json", "X-Atlas-Token": existing["token"]}, method="POST")
        with LOCAL_HTTP.open(request, timeout=3):
            print("Recall 服务已停止。")
        return
    if existing:
        if Path(existing["data_dir"]).resolve() != directory or existing.get("instance") != args.instance:
            raise SystemExit("这个端口上的 Recall 使用另一个索引目录，请通过 --port 指定不同端口。")
        print("Recall 已运行：" + url)
        if args.open:
            webbrowser.open(url)
        return
    roots = json.loads(Path(args.config).read_text(encoding="utf-8")) if args.config else None
    if args.background:
        directory.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-B", str(APP_DIR.parent / "app.py"), "--data-dir", str(directory), "--port", str(args.port), "--interval", str(args.interval), "--instance", args.instance]
        if args.config:
            command.extend(["--config", str(Path(args.config).resolve())])
        with (directory / "server.log").open("ab") as log:
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            process = subprocess.Popen(command, stdout=log, stderr=log, start_new_session=os.name != "nt", creationflags=flags)
        for _ in range(60):
            child = bootstrap(args.port)
            if child and child.get("instance") == args.instance and Path(child["data_dir"]).resolve() == directory:
                print("Recall：" + url)
                if args.open:
                    webbrowser.open(url)
                return
            if process.poll() is not None:
                raise SystemExit("服务未启动，请查看索引目录中的 server.log。")
            time.sleep(0.1)
        raise SystemExit("服务启动较慢，请稍后打开 " + url)
    store = Store(directory)
    indexer = Indexer(store, roots)
    if args.import_file:
        source = Path(args.import_file)
        sessions = normalize_import(source.read_text(encoding="utf-8"), args.provider, source.name)
        save_imports(store, sessions)
    if args.scan or args.import_file:
        indexer.scan()
        print(json.dumps({"stats": store.stats(), "index": indexer.status()}, ensure_ascii=False))
        return
    try:
        server = Server(("127.0.0.1", args.port), store, indexer, args.instance)
    except OSError:
        raise SystemExit(f"端口 {args.port} 已占用，可以通过 --port 指定其他端口。")
    def watch():
        while not server.stopping.is_set():
            indexer.scan()
            server.stopping.wait(max(10, args.interval))
    threading.Thread(target=watch, name="conversation-indexer", daemon=True).start()
    def stop(*_):
        server.stopping.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, stop)
    print("Recall：" + url, flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        server.stopping.set()
        server.server_close()


if __name__ == "__main__":
    main()
