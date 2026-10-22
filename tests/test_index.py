import json
import tempfile
import unittest
from pathlib import Path

from recall.core import Indexer, Store, normalize_import, redact, save_imports, summarize
from recall.capture import capture
from recall.hooks import plan


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="recall-test-")
        self.base = Path(self.temp.name)
        self.store = Store(self.base / "data")
        self.roots = {"codex": str(self.base / "codex"), "claude": str(self.base / "claude"), "trae": str(self.base / "trae")}
        self.indexer = Indexer(self.store, self.roots)

    def tearDown(self):
        self.temp.cleanup()

    def codex_file(self, name, messages, native_id="native-session-123"):
        path = self.base / "codex" / "sessions" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        meta = {"type": "session_meta", "payload": {"id": native_id, "source": "vscode", "cwd": "/test/project-alpha", "timestamp": "2026-01-01T00:00:00Z"}}
        rows = [meta]
        for index, message in enumerate(messages):
            role, text = message[:2]
            identifier = message[2] if len(message) > 2 else name + str(index)
            rows.append({"type": "response_item", "ordinal": index+1, "timestamp": f"2026-01-01T00:0{index+1}:00Z",
                         "payload": {"id": identifier, "type": "message", "role": role, "phase": "final_answer" if role=="assistant" else "",
                                     "content": [{"type": "output_text" if role=="assistant" else "input_text", "text": text}]}})
        self.write(path, rows)
        return path

    @staticmethod
    def write(path, rows):
        path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows)+"\n")

    def scan(self):
        self.assertTrue(self.indexer.scan())
        self.assertEqual(self.indexer.status()["errors"], [])

    def test_codex_messages_only_and_cjk_search(self):
        path = self.codex_file("one.jsonl", [("user", "数据库连接池超时，请排查。"), ("assistant", "超时来自连接池等待，已定位到慢查询。")])
        with path.open("a") as file:
            file.write(json.dumps({"type":"response_item","payload":{"type":"function_call_output","output":"tool-only-needle"}})+"\n")
            file.write(json.dumps({"type":"event_msg","payload":{"type":"agent_message","message":"duplicate-only-needle"}})+"\n")
            file.write(json.dumps({"type":"response_item","payload":{"type":"message","role":"developer","content":[{"type":"input_text","text":"system-only-needle"}]}})+"\n")
        self.scan()
        self.assertEqual(self.store.stats()["total"],1)
        self.assertEqual(self.store.stats()["messages"],2)
        self.assertEqual(self.store.search("池超时")["total"],1)
        self.assertEqual(self.store.search("timeout sql")["total"],1)
        self.assertEqual(self.store.search("tool-only-needle")["total"],0)
        self.assertEqual(self.store.search("duplicate-only-needle")["total"],0)
        self.assertEqual(self.store.search("system-only-needle")["total"],0)

    def test_continuation_dedup_and_subagent_exclusion(self):
        shared=("user","生命周期 Hook 排查","same-message")
        self.codex_file("one.jsonl",[shared])
        self.codex_file("two.jsonl",[shared,("assistant","回调协议已检查","second-message")])
        path=self.codex_file("agent.jsonl",[("user","subagent-only-needle")],"subagent-session")
        rows=[json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        rows[0]["payload"]["source"]={"subagent":{"thread_spawn":{"parent_thread_id":"native-session-123"}}}
        self.write(path,rows)
        self.scan()
        self.assertEqual(self.store.stats()["total"],1)
        self.assertEqual(self.store.stats()["messages"],2)
        self.assertEqual(self.store.search("subagent-only-needle")["total"],0)
        self.assertEqual(len(self.store.detail("codex:native-session-123")["session"]["source_paths"]),2)

    def test_incremental_partial_line_and_no_repeated_reads(self):
        path=self.codex_file("one.jsonl",[("user","初始问题"),("assistant","初始回复"*30)])
        self.scan()
        self.assertGreater(self.indexer.status()["bytes_read"],0)
        self.scan()
        self.assertEqual(self.indexer.status()["bytes_read"],0)
        row={"type":"response_item","ordinal":3,"timestamp":"2026-01-01T00:03:00Z","payload":{"type":"message","id":"appended-message","role":"user","content":[{"type":"input_text","text":"新问题 append-needle"}]}}
        encoded=json.dumps(row,ensure_ascii=False).encode()
        with path.open("ab") as file:file.write(encoded[:len(encoded)//2])
        self.scan()
        self.assertEqual(self.store.stats()["messages"],2)
        with path.open("ab") as file:file.write(encoded[len(encoded)//2:]+b"\n")
        self.scan()
        self.assertEqual(self.store.stats()["messages"],3)
        self.assertEqual(self.store.search("append-needle")["total"],1)
        self.assertLess(self.indexer.status()["bytes_read"],path.stat().st_size)

    def test_notes_and_favorites_survive_resync(self):
        self.codex_file("one.jsonl",[("user","缓存问题"),("assistant","缓存已定位")])
        self.scan()
        self.store.annotate("codex:native-session-123",favorite=True,notes="独立经验 personal-note-needle")
        self.scan()
        self.assertEqual(self.store.search("personal-note-needle",favorite=True)["total"],1)
        self.assertTrue(self.store.detail("codex:native-session-123")["session"]["favorite"])

    def test_claude_ignores_thinking_and_tool_results(self):
        path=self.base/"claude"/"project"/"claude-session.jsonl"
        path.parent.mkdir(parents=True)
        rows=[
            {"type":"user","uuid":"1","cwd":"/test/claude-project","timestamp":"2026-01-01T00:00:00Z","message":{"content":"请查权限错误"}},
            {"type":"assistant","uuid":"2","timestamp":"2026-01-01T00:01:00Z","message":{"model":"test-model","content":[{"type":"thinking","thinking":"hidden-thinking-needle"},{"type":"tool_use","input":{"q":"tool-input-needle"}},{"type":"text","text":"检查权限和授权范围"}]}},
            {"type":"user","uuid":"3","message":{"content":[{"type":"tool_result","content":"tool-result-needle"}]}},
            {"type":"user","uuid":"4","isSidechain":True,"message":{"content":"sidechain-only-needle"}},
            {"type":"ai-title","aiTitle":"权限诊断"}
        ]
        self.write(path,rows)
        self.scan()
        detail=self.store.detail("claude:claude-session")
        self.assertEqual(detail["session"]["title"],"权限诊断")
        self.assertEqual(detail["session"]["project"],"claude-project")
        self.assertEqual(detail["total_messages"],2)
        for query in ["hidden-thinking-needle","tool-input-needle","tool-result-needle","sidechain-only-needle"]:
            self.assertEqual(self.store.search(query)["total"],0)

    def test_import_stable_identity_and_no_phantom_sessions(self):
        raw={"provider":"codex","native_id":"imported-native","title":"导入经验","messages":[{"role":"user","content":"回滚部署问题"},{"role":"assistant","content":"保留部署回滚方案"}]}
        items=normalize_import(json.dumps(raw,ensure_ascii=False),"other","example.json")
        save_imports(self.store,items)
        self.scan()
        self.assertEqual(self.store.stats()["total"],1)
        self.assertEqual(self.store.detail("codex:imported-native")["total_messages"],2)
        save_imports(self.store,items)
        self.scan()
        self.assertEqual(self.store.stats()["total"],1)
        self.assertEqual(self.store.stats()["messages"],2)

    def test_chatgpt_only_selected_branch_and_claude_export(self):
        raw={"id":"native-gpt","title":"分支记录","current_node":"good","mapping":{
            "root":{"parent":None,"children":["good","other"],"message":{"id":"u1","author":{"role":"user"},"content":{"parts":["原始问题"]}}},
            "good":{"parent":"root","children":[],"message":{"id":"a1","author":{"role":"assistant"},"content":{"parts":["最终选定的答复"]}}},
            "other":{"parent":"root","children":[],"message":{"id":"a2","author":{"role":"assistant"},"content":{"parts":["未选中分支"]}}}
        }}
        items=normalize_import(json.dumps(raw,ensure_ascii=False),"other","conversations.json")
        self.assertEqual(len(items[0]["messages"]),2)
        self.assertEqual(items[0]["metadata"]["provider"],"chatgpt")
        self.assertNotIn("未选中分支",str(items))
        exported=[{"uuid":"native-claude","name":"Claude 经验","chat_messages":[{"uuid":"m1","sender":"human","text":"模型调用问题"},{"uuid":"m2","sender":"assistant","text":"检查模型配置"}]}]
        items=normalize_import(json.dumps(exported,ensure_ascii=False),"other","conversations.json")
        self.assertEqual(items[0]["metadata"]["provider"],"claude")
        self.assertEqual(len(items[0]["messages"]),2)

    def test_literal_queries_and_code_snippets(self):
        self.codex_file("one.jsonl",[("user","检查这个参数"),("assistant","使用配置：\n```python\ntimeout_seconds = 30\n```\n控制请求等待。")])
        self.scan()
        result=self.store.search("timeout_seconds")
        self.assertEqual(result["total"],1)
        self.assertIn("timeout_seconds",result["sessions"][0]["match"]["text"])
        for query in ['"','\" OR 1=1 --',"(test)*",":", "' UNION SELECT * FROM sessions --"]:
            result=self.store.search(query)
            self.assertIn("sessions",result)

    def test_redaction_before_indexing(self):
        secret="sk-"+"x"*35
        self.codex_file("one.jsonl",[("user","调用参数 api_key="+secret),("assistant","Authorization: Bearer "+"a"*35)])
        self.scan()
        detail=self.store.detail("codex:native-session-123")
        self.assertNotIn(secret,str(detail))
        self.assertNotIn("a"*35,str(detail))
        self.assertEqual(self.store.search(secret)["total"],0)
        self.assertIn("[已隐藏]",redact("access_token="+"a"*35))

    def test_claude_import_and_codex_continuation_keep_original_provider(self):
        path=self.codex_file("mirror.jsonl",[("user","原始 Claude 问题"),("assistant","原始 Claude 回复"),("user","在 Codex 继续排查"),("assistant","Codex 新回复")])
        rows=[json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        for row in rows[1:3]:row["payload"].pop("id")
        self.write(path,rows)
        registry={"records":[{"imported_thread_id":"native-session-123","source_path":str(self.base/".claude"/"projects"/"gone"/"original-claude-id.jsonl"),"title":"原始 Claude 会话"}]}
        (self.base/"codex"/"external_agent_session_imports.json").write_text(json.dumps(registry))
        self.scan()
        self.assertEqual(self.store.stats()["total"],2)
        original=self.store.detail("claude:original-claude-id")
        continued=self.store.detail("codex:native-session-123")
        self.assertEqual(original["total_messages"],2)
        self.assertEqual(continued["total_messages"],2)
        self.assertEqual(original["session"]["provider"],"claude")
        self.assertEqual(original["session"]["resume_kind"],"mirror")
        self.assertEqual(self.store.search("Claude 问题",provider="codex")["total"],0)

    def test_legacy_tool_wrappers_do_not_become_messages(self):
        self.codex_file("legacy.jsonl",[("user","有效用户问题"),("assistant","[external_agent_tool_result]\nlegacy-tool-needle"),("assistant","[external_agent_tool_call: Bash]\nlegacy-call-needle"),("assistant","有效最终回复")])
        self.scan()
        self.assertEqual(self.store.stats()["messages"],2)
        self.assertEqual(self.store.search("legacy-tool-needle")["total"],0)

    def test_trae_event_capture_and_repeated_user_prompts(self):
        capture({"session_id":"trae-original-id","workspace_roots":["/test/trae-project"]},"SessionStart",self.store.directory)
        capture({"session_id":"trae-original-id","text_content":"请排查数据库超时"},"UserPromptSubmit",self.store.directory)
        capture({"session_id":"trae-original-id","last_assistant_message":"检查连接池等待，定位慢查询。"},"Stop",self.store.directory)
        capture({"session_id":"trae-original-id","text_content":"请排查数据库超时"},"UserPromptSubmit",self.store.directory)
        self.scan()
        detail=self.store.detail("trae:trae-original-id")
        self.assertEqual(detail["total_messages"],3)
        self.assertEqual(detail["session"]["project"],"trae-project")
        self.assertEqual(self.store.search("数据库 超时",provider="trae")["total"],1)

    def test_hook_install_is_additive_and_idempotent(self):
        config=self.base/"hooks.json"
        existing={"version":1,"hooks":{"Stop":[{"hooks":[{"type":"command","command":"existing-command"}]}],"Notification":[]}}
        config.write_text(json.dumps(existing))
        before,after=plan(config,self.store.directory)
        self.assertEqual(before,existing)
        self.assertEqual(after["hooks"]["Stop"][0],existing["hooks"]["Stop"][0])
        config.write_text(json.dumps(after))
        second_before,second_after=plan(config,self.store.directory)
        self.assertEqual(second_before,second_after)
        self.assertNotIn("abcdefghijk",redact("CFS_TOKEN=abcdefghijk"))

    def test_summary_uses_substantive_question_and_explicit_finding(self):
        messages=[{"role":"user","text":"你好"},{"role":"user","text":"数据库连接池发生超时，请定位等待原因。"},
                  {"role":"assistant","phase":"final","text":"调用链路：服务 → 网关 → 数据库。\n根因：连接池等待耗尽超时预算。\n需要调整连接池上限并验证慢查询。"}]
        summary,conclusion,_=summarize("数据库问题",messages)
        self.assertIn("数据库连接池发生超时",summary)
        self.assertTrue(conclusion.startswith("根因"))


if __name__=="__main__":
    unittest.main()
