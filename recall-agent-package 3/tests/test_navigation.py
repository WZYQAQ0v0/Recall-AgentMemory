import json
import shlex
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from recall.core import Indexer, Store, normalize_import, save_imports
from recall.navigation import Navigator, NavigationError, conversation_id, safe_web_url
from recall.server import Server

CODEX_ID = "019a0000-1234-7000-8000-000000000001"
CLAUDE_ID = "019a0000-1234-7000-8000-000000000002"


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="recall-navigation-")
        self.base = Path(self.temp.name).resolve()
        self.cwd = self.base / "项目 '引号' $(example) 空格"
        self.cwd.mkdir()
        self.store = Store(self.base / "data")
        self.roots = {"codex": str(self.base / "codex"), "claude": str(self.base / "claude")}
        self.indexer = Indexer(self.store, self.roots)
        self.targets = {"codex_app": "/fixture/Codex.app", "claude_cli": "/fixture/Claude Code/claude",
                        "codex_cli": "/fixture/codex", "terminal": "macos-terminal"}
        self.navigator = Navigator(self.store, self.roots, system="Darwin", home=self.base, targets=self.targets)

    def tearDown(self):
        self.temp.cleanup()

    def codex(self):
        path = Path(self.roots["codex"]) / "sessions" / "one.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {"type": "session_meta", "payload": {"id": CODEX_ID, "cwd": str(self.cwd), "source": "cli"}},
            {"type": "response_item", "payload": {"id": "question", "type": "message", "role": "user",
                                                "content": [{"type": "input_text", "text": "连接池超时怎么定位"}]}}
        ]
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        self.indexer.scan()
        return path

    def claude(self):
        path = Path(self.roots["claude"]) / "project" / (CLAUDE_ID + ".jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [{"type": "user", "uuid": "question", "sessionId": CLAUDE_ID, "cwd": str(self.cwd),
                 "message": {"role": "user", "content": "继续原 Claude 会话"}}]
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        self.indexer.scan()
        return path

    def import_session(self, provider, identity, url=""):
        data = {"id": identity, "provider": provider, "url": url, "messages": [{"role": "user", "content": "导入记录"}]}
        save_imports(self.store, normalize_import(json.dumps(data), filename="import.json"))
        self.indexer.scan()
        return self.store.detail(provider + ":" + identity)["session"]

    def test_native_codex_uses_the_existing_thread_id(self):
        self.codex()
        session = self.store.detail("codex:" + CODEX_ID)["session"]
        result = self.navigator.describe(session)
        self.assertEqual(result["url"], "codex://threads/" + CODEX_ID)
        self.assertEqual(result["label"], "打开原对话")
        with mock.patch.object(self.navigator, "run") as launch:
            outcome = self.navigator.open(session["key"])
        launch.assert_called_once_with(["/usr/bin/open", "-a", "/fixture/Codex.app", "codex://threads/" + CODEX_ID])
        self.assertTrue(outcome["opened"])

    def test_missing_native_file_disables_the_original_jump(self):
        path = self.codex()
        path.unlink()
        result = self.navigator.describe(self.store.detail("codex:" + CODEX_ID)["session"])
        self.assertFalse(result["available"])
        with mock.patch.object(self.navigator, "launch") as launch:
            with self.assertRaises(NavigationError):
                self.navigator.open("codex:" + CODEX_ID)
        launch.assert_not_called()

    def test_codex_identity_is_rechecked_before_launch(self):
        path = self.codex()
        path.write_text(json.dumps({"type": "session_meta", "payload": {"id": CLAUDE_ID}}))
        with mock.patch.object(self.navigator, "launch") as launch:
            with self.assertRaisesRegex(NavigationError, "身份"):
                self.navigator.open("codex:" + CODEX_ID)
        launch.assert_not_called()

    def test_imported_codex_uuid_does_not_open_an_unrelated_local_thread(self):
        session = self.import_session("codex", CODEX_ID)
        self.assertFalse(self.navigator.describe(session)["available"])

    def test_claude_resumes_without_sending_a_prompt_and_quotes_project_paths(self):
        self.claude()
        with mock.patch.object(self.navigator, "run") as launch:
            self.navigator.open("claude:" + CLAUDE_ID)
        script = next((self.store.directory / "launch").glob("*.command"))
        lines = script.read_text(encoding="utf-8").splitlines()
        self.assertEqual(shlex.split(lines[-2]), ["cd", "--", str(self.cwd)])
        self.assertEqual(shlex.split(lines[-1]), ["exec", self.targets["claude_cli"], "--resume", CLAUDE_ID])
        self.assertNotIn("--print", lines[-1])
        self.assertNotIn("--dangerously-skip-permissions", lines[-1])
        self.assertEqual(script.stat().st_mode & 0o777, 0o700)
        launch.assert_called_once_with(["/usr/bin/open", "-b", "com.apple.Terminal", str(script)])

    def test_missing_project_prevents_terminal_resume(self):
        self.claude()
        self.cwd.rmdir()
        session = self.store.detail("claude:" + CLAUDE_ID)["session"]
        result = self.navigator.describe(session)
        self.assertFalse(result["available"])
        self.assertIn("项目目录", result["reason"])

    def test_claude_header_identity_is_rechecked(self):
        path = self.claude()
        path.write_text(json.dumps({"type": "user", "sessionId": CODEX_ID}) + "\n")
        with mock.patch.object(self.navigator, "launch") as launch:
            with self.assertRaisesRegex(NavigationError, "身份"):
                self.navigator.open("claude:" + CLAUDE_ID)
        launch.assert_not_called()

    def test_mirror_is_labeled_and_keeps_the_claude_provider(self):
        path = self.codex()
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[1]["payload"].pop("id")
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
        registry = {"records": [{"imported_thread_id": CODEX_ID,
                                  "source_path": str(self.base / ".claude" / "projects" / "gone" / (CLAUDE_ID + ".jsonl"))}]}
        (Path(self.roots["codex"]) / "external_agent_session_imports.json").write_text(json.dumps(registry))
        self.indexer.scan()
        session = self.store.detail("claude:" + CLAUDE_ID)["session"]
        result = self.navigator.describe(session)
        self.assertEqual(result["label"], "打开 Codex 镜像")
        self.assertTrue(result["mirrored"])
        self.assertEqual(result["url"], "codex://threads/" + CODEX_ID)
        self.assertEqual(session["provider"], "claude")

    def test_chatgpt_native_export_generates_conversation_url(self):
        session = self.import_session("chatgpt", CODEX_ID)
        self.assertEqual(self.navigator.describe(session)["url"], "https://chatgpt.com/c/" + CODEX_ID)

    def test_malicious_import_url_cannot_execute_a_command_or_open_settings(self):
        for url in [
            "javascript:alert(1)", "file:///private/tmp/attack.command", "command:example",
            "codex://threads/" + CODEX_ID + "?prompt=run",
            "https://claude.ai.evil.test/chat/" + CLAUDE_ID,
            "https://secret:password@claude.ai/chat/" + CLAUDE_ID,
            "https://claude.ai/chat/" + CLAUDE_ID + "?token=secret",
            "https://claude.ai/chat/" + CLAUDE_ID + "#fragment",
            "https://trae.cn/payment/123456789",
        ]:
            self.assertIsNone(safe_web_url("claude" if "claude" in url else "trae", url), url)
        session = self.import_session("claude", CLAUDE_ID, "file:///private/tmp/attack.command")
        self.assertFalse(self.navigator.describe(session)["available"])

    def test_trae_without_an_original_link_keeps_readback_instead_of_teleporting(self):
        session = self.import_session("trae", "existing-trae-session")
        result = self.navigator.describe(session)
        self.assertFalse(result["available"])
        self.assertEqual(result["label"], "查看原文")
        self.assertIn("Trae", result["reason"])
        self.assertEqual(safe_web_url("trae", "https://trae.cn/share/abcdefgh1234"), "https://trae.cn/share/abcdefgh1234")

    def test_repeated_clicks_do_not_spawn_duplicate_terminals(self):
        self.claude()
        with mock.patch.object(self.navigator, "launch") as launch:
            first = self.navigator.open("claude:" + CLAUDE_ID)
            second = self.navigator.open("claude:" + CLAUDE_ID)
        self.assertTrue(first["opened"])
        self.assertTrue(second["already_requested"])
        self.assertEqual(launch.call_count, 1)

    def test_unsafe_native_id_never_becomes_a_launch_argument(self):
        for value in ["../../other", "--help", "uuid; touch /tmp/file", CODEX_ID + "?prompt=x", ""]:
            self.assertIsNone(conversation_id(value))

    def test_http_requires_token_and_accepts_only_stored_session_id(self):
        self.codex()
        server = Server(("127.0.0.1", 0), self.store, self.indexer)
        server.navigator = self.navigator
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            for payload, token, status in [
                ({"id": "codex:" + CODEX_ID}, "", 403),
                ({"id": "codex:" + CODEX_ID, "command": "execute"}, server.token, 400),
                ({"id": "not-indexed"}, server.token, 404),
            ]:
                request = urllib.request.Request("http://127.0.0.1:" + str(port) + "/api/open-session",
                                                 data=json.dumps(payload).encode(), method="POST",
                                                 headers={"Content-Type": "application/json", "X-Atlas-Token": token})
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(request, timeout=3)
                self.assertEqual(caught.exception.code, status)
            with mock.patch.object(self.navigator, "launch") as launch:
                request = urllib.request.Request("http://127.0.0.1:" + str(port) + "/api/open-session",
                                                 data=json.dumps({"id": "codex:" + CODEX_ID}).encode(), method="POST",
                                                 headers={"Content-Type": "application/json", "X-Atlas-Token": server.token})
                with urllib.request.urlopen(request, timeout=3) as response:
                    result = json.load(response)
            self.assertTrue(result["opened"])
            launch.assert_called_once()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
