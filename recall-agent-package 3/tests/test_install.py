import base64
import copy
import hashlib
import json
import os
import plistlib
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from recall import services, __version__
from recall.core import Store, normalize_import, save_imports, Indexer
from recall.doctor import doctor
from recall.hooks import edit_config
from recall.install import install, load_state, plan_install, stop_installation, uninstall
from recall.paths import Layout, detect_sources
from recall.portable import write_json
from recall.runtime import get_json, owned_service, verify_package
from scripts.build_release import build_manifest

ROOT = Path(__file__).resolve().parent.parent


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="recall-install-test-")
        self.base = Path(self.temp.name).resolve()
        self.home = self.base / "收件人 用户"
        self.layout = Layout.resolve(self.home, self.base / "安装 100%" / "Recall")
        self.config = self.home / ".trae-cn" / "hooks.json"
        self.external = {"version": 1, "unrelated": True, "hooks": {
            "Stop": [{"matcher": "*", "hooks": [{"type": "command", "command": "external-command"}]}],
            "Notification": []}}
        write_json(self.config, self.external)
        source = self.home / ".codex" / "sessions" / "fixture.jsonl"
        source.parent.mkdir(parents=True)
        rows = [
            {"type": "session_meta", "payload": {"id": "fixture-codex-session", "cwd": str(self.home / "项目 空格"),
                                                "timestamp": "2026-01-01T00:00:00Z", "source": "cli"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "id": "u1",
                                                "content": [{"type": "input_text", "text": "数据库连接池超时该怎么定位？"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant", "id": "a1", "phase": "final",
                                                "content": [{"type": "output_text", "text": "根因：连接池等待耗尽了超时预算。"}]}},
        ]
        source.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
        self.layouts = [self.layout]

    def tearDown(self):
        for layout in self.layouts:
            try:
                state = load_state(layout)
                if state:
                    services.stop(state)
            except Exception:
                pass
        self.temp.cleanup()

    def run_install(self, layout=None, **kwargs):
        return install(layout or self.layout, source=kwargs.pop("source", ROOT), no_autostart=True, **kwargs)

    def clone_source(self):
        destination = self.base / ("incoming-" + str(len(list(self.base.glob("incoming-*")))))
        shutil.copytree(ROOT, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        return destination

    def annotate(self):
        store = Store(self.layout.data)
        self.assertTrue(store.annotate("codex:fixture-codex-session", notes="可复用的连接池排查经验", favorite=True))
        return store

    def test_plan_is_readonly_and_detects_only_fixture_home(self):
        before = self.config.read_bytes()
        result = plan_install(self.layout, source=ROOT, no_autostart=True)
        self.assertFalse(self.layout.base.exists())
        self.assertEqual(before, self.config.read_bytes())
        self.assertEqual(result["targets"][0]["config_path"], str(self.config))
        self.assertFalse(result["detection"]["detected"]["claude"])
        self.assertEqual(Path(result["roots"]["codex"]), self.home / ".codex")

    def test_install_occupied_port_search_and_synthetic_evidence(self):
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            preferred = busy.getsockname()[1]
            result = self.run_install(port=preferred)
            state = load_state(self.layout)
            self.assertNotEqual(state["port"], preferred)
            self.assertTrue(result["doctor"]["ready"])
            self.assertTrue(result["doctor"]["pipeline_smoke"]["passed"])
            self.assertFalse(result["doctor"]["trae_live_events"]["observed"])
            self.assertTrue(result["doctor"]["trae_hooks"][0]["configured"])
            self.assertEqual(get_json(state["port"], "/api/search?q=database")["total"], 1)
            self.assertEqual(busy.getsockname()[1], preferred)
            self.assertNotIn('"token"', json.dumps(result))

    def test_reinstall_preserves_annotations_custom_roots_and_hook_bytes(self):
        self.run_install()
        old = load_state(self.layout)
        store = self.annotate()
        roots = json.loads(self.layout.roots_file.read_text(encoding="utf-8"))
        roots["claude"] = str(self.home / "自选历史目录")
        roots["custom_source"] = str(self.home / "other")
        write_json(self.layout.roots_file, roots)
        hook_bytes = self.config.read_bytes()
        backups = sorted((self.layout.config / "hook-backups").glob("*.json"))
        self.run_install()
        current = load_state(self.layout)
        self.assertEqual(current["install_id"], old["install_id"])
        self.assertEqual(current["port"], old["port"])
        self.assertEqual(json.loads(self.layout.roots_file.read_text(encoding="utf-8")), roots)
        self.assertEqual(hook_bytes, self.config.read_bytes())
        self.assertEqual(backups, sorted((self.layout.config / "hook-backups").glob("*.json")))
        detail = store.detail("codex:fixture-codex-session")["session"]
        self.assertTrue(detail["favorite"])
        self.assertIn("可复用", detail["notes"])
        self.assertEqual(store.search("排查经验")["total"], 1)
        self.assertFalse(list(self.layout.base.glob(".app-previous-*")))

    def test_upgrade_preserves_identity_and_index(self):
        self.run_install()
        old = load_state(self.layout)
        self.annotate()
        source = self.clone_source()
        init = source / "recall" / "__init__.py"
        init.write_text(init.read_text().replace(__version__, "9.0.1"), encoding="utf-8")
        build_manifest(source)
        result = self.run_install(source=source)
        self.assertEqual(result["version"], "9.0.1")
        self.assertEqual(result["doctor"]["service"]["version"], "9.0.1")
        self.assertEqual(load_state(self.layout)["install_id"], old["install_id"])
        self.assertIn("可复用", Store(self.layout.data).detail("codex:fixture-codex-session")["session"]["notes"])

    def test_installed_control_works_after_incoming_folder_is_deleted(self):
        source = self.clone_source()
        result = self.run_install(source=source)
        shutil.rmtree(source)
        stop_installation(self.layout)
        command = [sys.executable, "-B", result["control"]]
        started = subprocess.run(command + ["start", "--json"], capture_output=True, text=True, encoding="utf-8", timeout=25)
        self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
        checked = subprocess.run(command + ["doctor", "--json"], capture_output=True, text=True, encoding="utf-8", timeout=25)
        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertTrue(json.loads(checked.stdout)["ready"])
        self.assertTrue(owned_service(load_state(self.layout)))

    def test_failed_reinstall_restores_old_code_service_and_hooks(self):
        self.run_install()
        before = load_state(self.layout)
        self.annotate()
        before_hooks = self.config.read_bytes()
        source = self.clone_source()
        init = source / "recall" / "__init__.py"
        init.write_text(init.read_text().replace(__version__, "9.0.7"), encoding="utf-8")
        build_manifest(source)
        real_start = services.start
        attempts = []

        def fail_once(state):
            attempts.append(state["version"])
            if len(attempts) == 1:
                raise RuntimeError("fixture startup failure")
            return real_start(state)

        with mock.patch("recall.install.services.start", side_effect=fail_once):
            with self.assertRaisesRegex(RuntimeError, "fixture startup failure"):
                self.run_install(source=source)
        restored = load_state(self.layout)
        self.assertEqual(before["install_id"], restored["install_id"])
        self.assertEqual(before["version"], restored["version"])
        self.assertEqual(attempts, ["9.0.7", __version__])
        self.assertTrue(owned_service(restored))
        self.assertEqual(before_hooks, self.config.read_bytes())
        self.assertEqual(Store(self.layout.data).search("排查经验")["total"], 1)
        verify_package(self.layout.app)
        self.assertFalse(list(self.layout.base.glob(".app-staging-*")))
        self.assertFalse(list(self.layout.base.glob(".app-previous-*")))

    @unittest.skipIf(os.name == "nt", "Unix shell bootstrap; Windows has its own entry point")
    def test_unix_bootstrap_runs_from_another_directory(self):
        source = self.clone_source()
        renamed = self.base / "待安装代码 空格"
        source.rename(renamed)
        command = ["sh", str(renamed / "install.sh"), "--home", str(self.home), "--prefix", str(self.layout.base),
                   "--no-autostart", "--no-open", "--json"]
        completed = subprocess.run(command, cwd=self.base, capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        result = json.loads(completed.stdout)
        self.assertTrue(result["doctor"]["ready"])
        self.assertTrue(owned_service(load_state(self.layout)))

    def test_uninstall_removes_only_its_hooks_and_retains_data(self):
        self.run_install()
        self.annotate()
        state = load_state(self.layout)
        completed = subprocess.run([sys.executable, "-B", str(self.layout.app / "recallctl.py"), "uninstall", "--json"],
                                   cwd=self.layout.app, capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        result = json.loads(completed.stdout)
        self.assertTrue(result["data_retained"])
        self.assertFalse(self.layout.app.exists())
        self.assertFalse(owned_service(state))
        self.assertEqual(json.loads(self.config.read_text(encoding="utf-8")), self.external)
        self.assertEqual(Store(self.layout.data).search("排查经验")["total"], 1)
        self.run_install()
        self.assertEqual(load_state(self.layout)["install_id"], state["install_id"])
        self.assertTrue(Store(self.layout.data).detail("codex:fixture-codex-session")["session"]["favorite"])

    def test_unrelated_application_directory_is_never_overwritten(self):
        self.layout.app.mkdir(parents=True)
        marker = self.layout.app / "foreign.txt"
        marker.write_text("external data")
        with self.assertRaisesRegex(RuntimeError, "unrelated"):
            self.run_install()
        self.assertEqual(marker.read_text(), "external data")
        self.assertEqual(json.loads(self.config.read_text()), self.external)

    def test_checksum_failure_prevents_installation_changes(self):
        source = self.clone_source()
        file = source / "recall" / "core.py"
        file.write_text(file.read_text(encoding="utf-8") + "\n# corrupted\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "missing or changed"):
            self.run_install(source=source)
        self.assertFalse(self.layout.app.exists())
        self.assertEqual(json.loads(self.config.read_text()), self.external)

    def test_shutdown_checks_installation_identity_not_just_port(self):
        self.run_install(no_hooks=True)
        first = load_state(self.layout)
        second_layout = Layout.resolve(self.home, self.base / "second")
        self.layouts.append(second_layout)
        self.run_install(layout=second_layout, no_hooks=True, port=first["port"])
        second = load_state(second_layout)
        wrong = copy.deepcopy(first)
        wrong["port"] = second["port"]
        services.stop(wrong)
        self.assertTrue(owned_service(second))
        self.assertTrue(owned_service(first))

    def test_doctor_detects_missing_hook_without_inventing_callback(self):
        self.run_install()
        write_json(self.config, self.external)
        result = doctor(self.layout)
        self.assertFalse(result["ready"])
        self.assertFalse(result["trae_hooks"][0]["configured"])
        self.assertFalse(result["trae_live_events"]["observed"])
        self.assertTrue(result["pipeline_smoke"]["passed"])

    def test_local_service_ignores_network_proxy_environment(self):
        self.run_install()
        state = load_state(self.layout)
        with mock.patch.dict(os.environ, {"http_proxy": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1",
                                         "no_proxy": "", "NO_PROXY": ""}):
            self.assertTrue(owned_service(state))
            self.assertTrue(doctor(self.layout, smoke=False)["ready"])

    def test_http_rejects_cross_site_and_uncredentialed_writes(self):
        self.run_install()
        state = load_state(self.layout)
        url = "http://127.0.0.1:" + str(state["port"])
        for request in [
            urllib.request.Request(url + "/api/status", headers={"Sec-Fetch-Site": "cross-site"}),
            urllib.request.Request(url + "/api/annotate", data=b"{}", method="POST", headers={"Content-Type": "application/json"}),
        ]:
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(request, timeout=3)
            self.assertEqual(caught.exception.code, 403)
        with urllib.request.urlopen(url, timeout=3) as response:
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])


class PortabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="recall-portability-")
        self.home = Path(self.temp.name).resolve() / "用户 '$ 100%"

    def tearDown(self):
        self.temp.cleanup()

    def state(self, system):
        layout = Layout.resolve(self.home, self.home / "Recall", system=system)
        value = {"python": str(self.home / "Python 空格" / "python.exe"), "app_dir": str(layout.app),
                 "data_dir": str(layout.data), "roots_file": str(layout.roots_file), "log_dir": str(layout.logs),
                 "install_id": "00000000-0000-0000-0000-000000000001", "port": 19877, "service_label": layout.service_label}
        return layout, value

    def test_mac_descriptor_uses_argument_array(self):
        layout, state = self.state("Darwin")
        descriptor = services.startup_descriptor(layout, state)
        parsed = plistlib.loads(descriptor["content"].encode())
        self.assertEqual(parsed["ProgramArguments"], services.server_command(state))
        self.assertEqual(parsed["WorkingDirectory"], state["app_dir"])
        self.assertTrue(parsed["RunAtLoad"])

    def test_linux_descriptors_escape_literal_percent_and_dollar(self):
        layout, state = self.state("Linux")
        systemd = services.startup_descriptor(layout, state, "systemd")
        desktop = services.startup_descriptor(layout, state, "xdg-autostart")
        self.assertIn("100%%", systemd["content"])
        self.assertIn("$$", systemd["content"])
        self.assertIn("100%%", desktop["content"])
        self.assertIn('Exec="', desktop["content"])
        self.assertNotIn("Exec='", desktop["content"])

    def test_windows_startup_preserves_unicode_and_literal_percent(self):
        layout, state = self.state("Windows")
        descriptor = services.startup_descriptor(layout, state)
        encoded = descriptor["content"].split("-EncodedCommand ", 1)[1].split('"', 1)[0]
        decoded = base64.b64decode(encoded).decode("utf-16-le")
        self.assertIn("用户 ''$ 100%", decoded)
        self.assertIn("--instance", decoded)
        self.assertIn(state["install_id"], decoded)
        self.assertNotIn(state["app_dir"], descriptor["content"])

    def test_unrelated_or_modified_startup_files_are_kept(self):
        layout, state = self.state("Darwin")
        descriptor = services.startup_descriptor(layout, state)
        path = Path(descriptor["path"])
        path.parent.mkdir(parents=True)
        path.write_text("external startup")
        with self.assertRaisesRegex(RuntimeError, "unrelated"):
            services.check_registration(state, descriptor)
        state["startup"] = {key: descriptor[key] for key in ["path", "kind", "label"]}
        state["startup"]["sha256"] = hashlib.sha256(b"prior content").hexdigest()
        with self.assertRaisesRegex(RuntimeError, "another owner"):
            services.unregister_startup(state)
        self.assertEqual(path.read_text(), "external startup")

    def test_multiple_trae_editions_are_detected_without_real_home_env(self):
        for folder in [".trae", ".trae-cn"]:
            (self.home / folder).mkdir(parents=True)
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.home / "wrong-root")}):
            result = detect_sources(Layout.resolve(self.home, system="Linux"))
        self.assertEqual(len(result["trae_clients"]), 2)
        self.assertEqual(Path(result["roots"]["codex"]), self.home / ".codex")

    def test_removing_hooks_does_not_create_missing_events(self):
        original = {"version": 1, "hooks": {"Notification": []}}
        result = edit_config(original, Path("/fixture/helper.py"), Path("/fixture/data"), "python", "absent", remove=True)
        self.assertEqual(result, original)

    def test_custom_provider_import_is_searchable(self):
        store = Store(Path(self.temp.name) / "data")
        save_imports(store, normalize_import(json.dumps({"id": "custom-session", "messages": [
            {"role": "user", "content": "自定义来源测试 keywordneedle"},
            {"role": "assistant", "content": "根因：保留了来源身份。"}]}), provider="my-agent", filename="test.json"))
        indexer = Indexer(store, {})
        self.assertTrue(indexer.scan())
        self.assertEqual(store.search("keywordneedle", provider="my-agent")["total"], 1)
        self.assertEqual(store.detail("my-agent:custom-session")["session"]["provider_label"], "my-agent")

    def test_imported_codex_record_has_no_unverified_native_resume_command(self):
        store = Store(Path(self.temp.name) / "data")
        save_imports(store, normalize_import(json.dumps({"id": "external-codex-id", "messages": [
            {"role": "user", "content": "从其他电脑导入的会话"}]}), provider="codex", filename="test.json"))
        Indexer(store, {}).scan()
        self.assertEqual(store.detail("codex:external-codex-id")["session"]["resume_command"], "")


if __name__ == "__main__":
    unittest.main()
