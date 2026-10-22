"""Local, read-only conversation ingestion and an incrementally updated search index."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shlex
import sqlite3
import sys
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from contextlib import contextmanager
from .paths import Layout, detect_sources

PROVIDERS = {"codex": "Codex", "claude": "Claude", "trae": "Trae", "chatgpt": "ChatGPT", "other": "其他 AI",
             "cursor": "Cursor", "gemini": "Gemini", "copilot": "GitHub Copilot", "opencode": "OpenCode", "doubao": "豆包"}
MAX_LINE = 8 * 1024 * 1024
ALIASES = [
    ["超时", "timeout", "deadline"], ["权限", "permission", "authorization", "acl"],
    ["钩子", "hook", "webhook"], ["部署", "deploy", "deployment"],
    ["数据库", "database", "sql"], ["并发", "concurrency", "race"],
    ["缓存", "cache"], ["评估", "evaluation", "eval"], ["回滚", "rollback"],
    ["内存", "memory"], ["日历", "calendar"], ["模型", "model"],
    ["检索", "search", "retrieval"], ["日志", "log", "logs"],
    ["前端", "frontend"], ["后端", "backend"], ["实验", "experiment"],
]
TECH_WORDS = (
    "生命周期 评估器 多维表格 数据库 工作流 回调 接口 超时 权限 部署 回滚 并发 缓存 内存 "
    "架构 检索 索引 搜索 看板 会话 日历 会议 模型 实验 训练 推理 成本 预算 延迟 性能 "
    "验证 验收 审查 发布 告警 排查 诊断 根因 迁移 认证 测试 服务 前端 后端 组件 布局 "
    "调研 论文 算法 去重 摘要 关键词 飞书 数据集 版本 分支 消息 路由 队列 事务 锁等待 "
    "知识管理 完整闭环 图表 自动化 隔离 参数 协议 业务 设计 效率 记忆 配置"
).split()
STOP_WORDS = set((
    "the and that this with from have has are was were will would should can could you your "
    "for not yes true false null none all any but into through about using used use also only "
    "http https com org www json text type user assistant final message content return function "
    "const let var new print output input path cwd file files home users documents "
    "code please here done update current error status result data string list dict python3 "
    "read write local value name key id session timestamp instructions tool tools api get set "
    "markdown task work run now need next then first last max min default source after before "
    "it is in on to of a an as at be if or no so we he she they them us my me per"
).split())


def stamp(value: Any, default: float = 0) -> float:
    if isinstance(value, (float, int)):
        return float(value) / 1000 if value > 10**11 else float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return default


def iso(value: float) -> str:
    return datetime.fromtimestamp(value or time.time(), timezone.utc).isoformat()


def redact(text: str) -> str:
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", "[密钥已隐藏]", text, flags=re.S)
    text = re.sub(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}\b", "[密钥已隐藏]", text)
    text = re.sub(r"\bAKIA[A-Z0-9]{16}\b", "[密钥已隐藏]", text)
    text = re.sub(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]{10,}", r"\1[已隐藏]", text)
    text = re.sub(r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|app[_-]?secret|password|secret[_-]?key|authorization)\b[\"']?\s*[:=]\s*[\"']?)([A-Za-z0-9._~+/=-]{12,})", r"\1[已隐藏]", text)
    text = re.sub(r"(?i)(\b[A-Za-z][A-Za-z0-9_]*_(?:api_key|token|secret|password)\b[\"']?\s*[:=]\s*[\"']?)([A-Za-z0-9._~+/=-]{8,})", r"\1[已隐藏]", text)
    text = re.sub(r"(?i)([?&](?:token|access_token|signature|x-amz-signature|x-tos-signature)=)[^&#\s]+", r"\1[已隐藏]", text)
    text = re.sub(r"(?im)^(\s*(?:set-cookie|cookie)\s*:\s*).+$", r"\1[已隐藏]", text)
    return text


def clean(text: str, role: str = "") -> str:
    if not isinstance(text, str):
        return ""
    if text.lstrip().startswith(("[external_agent_tool_", "[external_agent_thinking]", "<subagent_notification>")):
        return ""
    relay = re.search(r"<user_message>\s*(.*?)\s*</user_message>", text, re.S)
    if relay:
        text = relay.group(1)
    if "## My request:" in text:
        text = text.split("## My request:", 1)[1]
    elif role == "user" and text.lstrip().startswith("# AGENTS.md instructions"):
        return ""
    text = re.sub(r"<oai-mem-citation>.*?</oai-mem-citation>", "", text, flags=re.S)
    for tag in ["environment_context", "in-app-browser-context", "app-context", "system-reminder", "permissions instructions", "turn_aborted", "ide_opened_file", "ide_selection"]:
        text = re.sub(r"<" + re.escape(tag) + r"(?:\s[^>]*)?>.*?</" + re.escape(tag) + r">", "", text, flags=re.S)
    if "<send_user_message_question_reply>" in text:
        match = re.search(r"<send_user_message_question_reply>\s*(.*?)\s*</send_user_message_question_reply>", text, re.S)
        if match:
            try:
                items = json.loads(match.group(1))
                text = "\n".join(str(item.get("question", "")) + "\n" + str(item.get("answer", "")) for item in items if isinstance(item, dict))
            except (ValueError, TypeError):
                pass
    if role == "user" and text.strip().startswith(("[Request interrupted", "<turn_aborted>")):
        return ""
    return redact(text.strip())


def plain(text: str) -> str:
    text = re.sub(r":codex-file-citation\{[^}]*\}", "", text)
    text = re.sub(r"<image\s[^>]*>", " [图片] ", text)
    text = re.sub(r"```[^\n]*\n.*?```", " [代码片段] ", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(?m)^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*)", "", text)
    text = text.replace("**", "").replace("`", "")
    return re.sub(r"\s+", " ", text).strip()


def clip(text: str, limit: int = 180) -> str:
    text = plain(text)
    if len(text) <= limit:
        return text
    part = text[:limit]
    for sep in ["。", "；", ". ", "！", "？"]:
        at = part.rfind(sep)
        if at > limit * 0.55:
            return part[:at + len(sep)].strip()
    return part.rstrip(" ，,、;") + "…"


def short_title(text: str) -> str:
    text = plain(text)
    without_links = re.sub(r"https?://\S+", "", text).strip(" \\，,；;")
    if without_links:
        text = without_links
    if len(text) > 75:
        for marker in ["我想实现", "想实现", "我想做", "请帮我", "帮我实现"]:
            if marker in text:
                text = text[text.index(marker):]
                break
    return clip(text, 75)


def tokens(text: str) -> str:
    """CJK bigrams keep Chinese substring queries searchable in SQLite FTS5."""
    pieces = []
    for match in re.finditer(r"[A-Za-z0-9_]+|[\u3400-\u9fff]+", text.lower()):
        word = match.group()
        if "\u3400" <= word[0] <= "\u9fff":
            pieces.extend(word[i:i+2] for i in range(len(word)-1)) if len(word) > 1 else pieces.append(word)
        else:
            pieces.append(word)
    return " ".join(pieces)


def query_groups(query: str) -> list[list[str]]:
    groups = []
    for match in re.finditer(r'"([^"\n]+)"|(\S+)', query.strip()[:500]):
        term = (match.group(1) or match.group(2)).strip('"')
        if not tokens(term):
            continue
        aliases = next((group for group in ALIASES if term.lower() in group), [term])
        groups.append(list(dict.fromkeys([term] + aliases)))
        if len(groups) == 12:
            break
    return groups


def fts_query(groups: list[list[str]]) -> str:
    clauses = []
    for group in groups:
        phrases = ['"' + tokens(term).replace('"', '""') + '"' for term in group if tokens(term)]
        clauses.append("(" + " OR ".join(phrases) + ")")
    return " AND ".join(clauses)


def extract_keywords(title: str, messages: list[dict], project: str) -> list[str]:
    # Weight the question and actual conclusions above progress commentary.
    weighted = title + "\n" + title + "\n" + project + "\n"
    for msg in messages:
        if msg["role"] == "user" or msg.get("phase") in {"final", "final_answer"}:
            weighted += msg["text"] + "\n"
    if len(weighted) < 200:
        weighted += "\n".join(m["text"] for m in messages)
    weighted = re.sub(r"https?://\S+", " ", weighted)
    counts: Counter = Counter()
    forms = {}
    for word in re.findall(r"[A-Za-z][A-Za-z0-9_.-]{1,45}", weighted):
        key = word.lower().strip(".-_")
        if key in STOP_WORDS or len(key) < 2 or key in {"by", "do", "err", "purpose", "output", "metadata", "sender", "open_id"} or key.startswith(("ou_", "msg_", "call_")) or re.fullmatch(r"[a-f0-9]{8,}", key):
            continue
        if "." in key and key.endswith((".com", ".net", ".cn", ".org")):
            continue
        counts[key] += 1
        forms.setdefault(key, word.strip(".-_"))
    for word in TECH_WORDS:
        count = weighted.count(word)
        if count:
            counts[word] += count * 1.5
            forms[word] = word
    scores = []
    title_lower = title.lower()
    for key, count in counts.items():
        score = math.log1p(count)
        if key in title_lower:
            score += 2.6
        if key == project.lower():
            score += 0.8
        if key in {"验证", "配置", "设计", "业务", "版本", "服务", "测试", "代码"}:
            score *= 0.42
        scores.append((score, key))
    return [forms[key] for _, key in sorted(scores, reverse=True)[:8]]


def summarize(title: str, messages: list[dict]) -> tuple[str, str, list[str]]:
    users = [msg for msg in messages if msg["role"] == "user"]
    assistants = [msg for msg in messages if msg["role"] == "assistant"]
    finals = [msg for msg in assistants if msg.get("phase") in {"final", "final_answer"}]
    substantive = [msg for msg in users if len(plain(msg["text"])) >= 16]
    question = (substantive or users)[0]["text"] if users else title
    purpose = clip(question, 150)
    ending = (finals or assistants)[-1]["text"] if (finals or assistants) else ""
    conclusion = clip(ending, 280)
    candidates = []
    for part in re.split(r"(?<=[。！？])|\n\s*\n", ending):
        text = plain(part)
        if len(text) < 12:
            continue
        for pattern, weight in [(r"结论[：:]", 6), (r"原因(?:是|在于)", 6), (r"根因", 5),
                                (r"已定位", 4), (r"已修复", 4), (r"已完成", 3), (r"已生成", 3)]:
            match = re.search(pattern, text)
            if match:
                candidates.append((weight, text[match.start():]))
                break
    if candidates:
        conclusion = clip(max(candidates, key=lambda item: item[0])[1], 280)
    summary = purpose
    if conclusion and conclusion != purpose:
        summary = clip(purpose, 105) + "\n" + clip(conclusion, 155)
    sentences = [p.strip() for p in re.split(r"(?<=[。！？])|\n\s*\n", ending) if len(plain(p)) > 12]
    points = [clip(p, 170) for p in sentences[:3]]
    return summary, conclusion, points


def content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        item.get("text", "") for item in content if isinstance(item, dict)
        and item.get("type") in {"text", "input_text", "output_text"} and isinstance(item.get("text"), str)
    )


class Store:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "index.sqlite"
        with self.connection() as con:
            con.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    key TEXT PRIMARY KEY, provider TEXT NOT NULL, native_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '', original_title TEXT NOT NULL DEFAULT '',
                    cwd TEXT NOT NULL DEFAULT '', project TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL DEFAULT 0,
                    summary TEXT NOT NULL DEFAULT '', conclusion TEXT NOT NULL DEFAULT '',
                    points TEXT NOT NULL DEFAULT '[]', keywords TEXT NOT NULL DEFAULT '[]',
                    models TEXT NOT NULL DEFAULT '[]', favorite INTEGER NOT NULL DEFAULT 0,
                    notes TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0,
                    partial INTEGER NOT NULL DEFAULT 0, message_count INTEGER NOT NULL DEFAULT 0,
                    source_kind TEXT NOT NULL DEFAULT 'local', original_url TEXT NOT NULL DEFAULT '',
                    mirror_thread_id TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, session_key TEXT NOT NULL REFERENCES sessions(key),
                    source_path TEXT NOT NULL, source_offset INTEGER NOT NULL,
                    role TEXT NOT NULL, phase TEXT NOT NULL DEFAULT '', text TEXT NOT NULL,
                    timestamp REAL NOT NULL DEFAULT 0, sequence INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS messages_session ON messages(session_key, timestamp, sequence);
                CREATE TABLE IF NOT EXISTS source_files (
                    path TEXT PRIMARY KEY, provider TEXT NOT NULL, session_key TEXT NOT NULL,
                    inode INTEGER NOT NULL, offset INTEGER NOT NULL DEFAULT 0,
                    size INTEGER NOT NULL DEFAULT 0, mtime INTEGER NOT NULL DEFAULT 0,
                    prefix_hash TEXT NOT NULL DEFAULT ''
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
                    session_key UNINDEXED, title, summary, keywords, body, tokenize='unicode61'
                );
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            if "mirror_thread_id" not in {row[1] for row in con.execute("PRAGMA table_info(sessions)")}:
                con.execute("ALTER TABLE sessions ADD COLUMN mirror_thread_id TEXT NOT NULL DEFAULT ''")
        try:
            os.chmod(self.path, 0o600)
            os.chmod(directory, 0o700)
        except OSError:
            pass

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.path), timeout=20)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=20000")
        return con

    @contextmanager
    def connection(self):
        con = self.connect()
        try:
            with con:
                yield con
        finally:
            con.close()

    def ensure_session(self, con: sqlite3.Connection, info: dict) -> str:
        provider = info["provider"]
        native_id = str(info["native_id"])
        key = provider + ":" + native_id
        native_title = clean(str(info.get("title") or ""), "user")
        cwd = str(info.get("cwd") or "")
        project = info.get("project") or (Path(cwd).name if cwd else "未关联项目")
        con.execute("""INSERT INTO sessions(key,provider,native_id,title,original_title,cwd,project,created_at,updated_at,archived,source_kind,original_url,mirror_thread_id,partial)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET
            original_title=CASE WHEN excluded.original_title!='' THEN excluded.original_title ELSE sessions.original_title END,
            cwd=CASE WHEN excluded.cwd!='' THEN excluded.cwd ELSE sessions.cwd END,
            project=CASE WHEN excluded.cwd!='' OR excluded.project!='未关联项目' THEN excluded.project ELSE sessions.project END,
            archived=excluded.archived,
            source_kind=CASE WHEN excluded.source_kind='local' THEN 'local' ELSE sessions.source_kind END,
            mirror_thread_id=CASE WHEN excluded.mirror_thread_id!='' THEN excluded.mirror_thread_id ELSE sessions.mirror_thread_id END,
            partial=MAX(sessions.partial,excluded.partial),
            original_url=CASE WHEN excluded.original_url!='' THEN excluded.original_url ELSE sessions.original_url END""",
            (key, provider, native_id, clip(native_title, 110), native_title, cwd, project,
             stamp(info.get("created_at")), stamp(info.get("updated_at")), int(bool(info.get("archived"))),
             info.get("source_kind", "local"), redact(str(info.get("original_url") or "")), info.get("mirror_thread_id", ""), int(bool(info.get("partial")))))
        return key

    def put_message(self, con: sqlite3.Connection, key: str, msg: dict, path: str, offset: int) -> bool:
        role = msg.get("role")
        text = clean(msg.get("text", ""), role)
        if role not in {"user", "assistant"} or not text:
            return False
        when = stamp(msg.get("timestamp"))
        native = msg.get("id") or hashlib.sha256((str(when) + role + text).encode()).hexdigest()
        identifier = hashlib.sha256((key + ":" + str(native)).encode()).hexdigest()
        old = con.execute("SELECT text FROM messages WHERE id=?", (identifier,)).fetchone()
        if old and old["text"] == text:
            return False
        con.execute("""INSERT INTO messages(id,session_key,source_path,source_offset,role,phase,text,timestamp,sequence)
            VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET text=excluded.text,phase=excluded.phase""",
            (identifier, key, path, offset, role, msg.get("phase") or "", text, when, int(msg.get("sequence") or offset)))
        return True

    def rebuild(self, con: sqlite3.Connection, key: str, models: list | None = None):
        row = con.execute("SELECT * FROM sessions WHERE key=?", (key,)).fetchone()
        messages = [dict(m) for m in con.execute("SELECT role,phase,text,timestamp FROM messages WHERE session_key=? ORDER BY timestamp,sequence,rowid", (key,))]
        users = [m for m in messages if m["role"] == "user"]
        assistants = [m for m in messages if m["role"] == "assistant"]
        title = short_title(row["original_title"]) or (short_title(users[0]["text"]) if users else short_title(assistants[0]["text"]) if assistants else PROVIDERS.get(row["provider"], "AI") + " · 新会话")
        summary, conclusion, points = summarize(title, messages)
        keywords = extract_keywords(title, messages, row["project"])
        times = [m["timestamp"] for m in messages if m["timestamp"]]
        all_models = list(dict.fromkeys(json.loads(row["models"]) + (models or [])))
        created = min(times + ([row["created_at"]] if row["created_at"] else [])) if times or row["created_at"] else time.time()
        updated = max(times) if times else row["updated_at"] or created
        con.execute("UPDATE sessions SET title=?,summary=?,conclusion=?,points=?,keywords=?,models=?,created_at=?,updated_at=?,message_count=? WHERE key=?",
                    (title, summary, conclusion, json.dumps(points, ensure_ascii=False), json.dumps(keywords, ensure_ascii=False),
                     json.dumps(all_models, ensure_ascii=False), created, updated, len(messages), key))
        con.execute("DELETE FROM search_index WHERE session_key=?", (key,))
        body = "\n".join(m["text"] for m in messages) + "\n" + row["notes"] + "\n" + row["cwd"]
        con.execute("INSERT INTO search_index(session_key,title,summary,keywords,body) VALUES(?,?,?,?,?)",
                    (key, tokens(title), tokens(summary), tokens(" ".join(keywords)), tokens(body)))

    @staticmethod
    def session_dict(row: sqlite3.Row | dict) -> dict:
        data = dict(row)
        for field in ["keywords", "models", "points"]:
            if field in data and isinstance(data[field], str):
                data[field] = json.loads(data[field])
        data["provider_label"] = PROVIDERS.get(data["provider"], data["provider"])
        data["favorite"] = bool(data.get("favorite"))
        return data

    def search(self, query: str = "", provider: str = "", project: str = "", favorite: bool = False,
               days: int = 0, sort: str = "recent", offset: int = 0, limit: int = 24) -> dict:
        started = time.perf_counter()
        groups = query_groups(query)
        conditions, args = [], []
        join = ""
        if groups:
            join = " JOIN search_index ON s.key=search_index.session_key "
            conditions.append("search_index MATCH ?")
            args.append(fts_query(groups))
        if provider:
            conditions.append("s.provider=?")
            args.append(provider)
        if project:
            conditions.append("s.project=?")
            args.append(project)
        if favorite:
            conditions.append("s.favorite=1")
        if days:
            conditions.append("s.updated_at>=?")
            args.append(time.time() - days * 86400)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        order = "bm25(search_index,0,9,4,4,1),s.updated_at DESC" if groups and sort != "recent" else "s.updated_at DESC"
        with self.connection() as con:
            total = con.execute("SELECT COUNT(*) FROM sessions s" + join + where, args).fetchone()[0]
            rows = con.execute("SELECT s.* FROM sessions s" + join + where + " ORDER BY " + order + " LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
            data = []
            for row in rows:
                item = self.session_dict(row)
                if groups:
                    best = None
                    best_score = 0
                    for msg in con.execute("SELECT id,role,text,timestamp FROM messages WHERE session_key=?", (row["key"],)):
                        score = sum(any(term.lower() in msg["text"].lower() for term in group) for group in groups)
                        if score > best_score:
                            best, best_score = msg, score
                    if best:
                        item["match"] = {"message_id": best["id"], "role": best["role"], "text": excerpt(best["text"], groups), "timestamp": best["timestamp"]}
                data.append(item)
        return {"sessions": data, "total": total, "offset": offset, "limit": limit,
                "has_more": offset + limit < total, "query": query,
                "match_terms": list(dict.fromkeys(term for group in groups for term in group)),
                "search_ms": round((time.perf_counter() - started) * 1000, 1)}

    def detail(self, key: str, query: str = "", offset: int | None = None, limit: int = 24, focus: str = "") -> dict | None:
        with self.connection() as con:
            row = con.execute("SELECT * FROM sessions WHERE key=?", (key,)).fetchone()
            if not row:
                return None
            item = self.session_dict(row)
            total = con.execute("SELECT COUNT(*) FROM messages WHERE session_key=?", (key,)).fetchone()[0]
            if focus:
                identifiers = [m[0] for m in con.execute("SELECT id FROM messages WHERE session_key=? ORDER BY timestamp,sequence,rowid", (key,))]
                offset = max(0, identifiers.index(focus) - 2) if focus in identifiers else offset
            offset = max(0, total - limit) if offset is None else max(0, offset)
            messages = [dict(m) for m in con.execute("SELECT id,role,phase,text,timestamp FROM messages WHERE session_key=? ORDER BY timestamp,sequence,rowid LIMIT ? OFFSET ?", (key, limit, offset))]
            paths = list(dict.fromkeys([m[0] for m in con.execute("SELECT path FROM source_files WHERE session_key=? ORDER BY mtime DESC", (key,))] +
                    [m[0] for m in con.execute("SELECT DISTINCT source_path FROM messages WHERE session_key=?", (key,))]))
            item["source_paths"] = paths
            item["resume_command"] = ""
            item["resume_kind"] = "native"
            if row["provider"] in {"codex", "claude"} and re.fullmatch(r"[A-Za-z0-9_-]{8,100}", row["native_id"]):
                native_exists = (row["source_kind"] == "local" and any(Path(p).is_file() for p in paths)) if row["provider"] == "codex" else any(Path(p).stem == row["native_id"] and Path(p).is_file() for p in paths)
                if native_exists:
                    command = "codex resume " if row["provider"] == "codex" else "claude --resume "
                    item["resume_command"] = ("cd " + shlex.quote(row["cwd"]) + " && " if row["cwd"] else "") + command + shlex.quote(row["native_id"])
                    if sys.platform == "win32":
                        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
                        item["resume_command"] = ("Set-Location -LiteralPath " + quote(row["cwd"]) + "; " if row["cwd"] else "") + "& " + command + quote(row["native_id"])
                elif row["mirror_thread_id"]:
                    item["resume_kind"] = "mirror"
                    item["resume_command"] = ("cd " + shlex.quote(row["cwd"]) + " && " if row["cwd"] else "") + "codex resume " + shlex.quote(row["mirror_thread_id"])
                    if sys.platform == "win32":
                        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
                        item["resume_command"] = ("Set-Location -LiteralPath " + quote(row["cwd"]) + "; " if row["cwd"] else "") + "& codex resume " + quote(row["mirror_thread_id"])
            groups = query_groups(query)
            matches = []
            if groups:
                for m in con.execute("SELECT id,role,text,timestamp FROM messages WHERE session_key=? ORDER BY timestamp DESC", (key,)):
                    if any(term.lower() in m["text"].lower() for group in groups for term in group):
                        matches.append({"id": m["id"], "role": m["role"], "text": excerpt(m["text"], groups, 380), "timestamp": m["timestamp"]})
                        if len(matches) == 8:
                            break
            return {"session": item, "messages": messages, "matches": matches,
                    "total_messages": total, "offset": offset, "limit": limit,
                    "has_earlier": offset > 0, "has_later": offset + limit < total,
                    "match_terms": list(dict.fromkeys(term for group in groups for term in group))}

    def annotate(self, key: str, favorite: bool | None = None, notes: str | None = None) -> bool:
        with self.connection() as con:
            if not con.execute("SELECT 1 FROM sessions WHERE key=?", (key,)).fetchone():
                return False
            if favorite is not None:
                con.execute("UPDATE sessions SET favorite=? WHERE key=?", (int(favorite), key))
            if notes is not None:
                con.execute("UPDATE sessions SET notes=? WHERE key=?", (redact(notes[:20000]), key))
                self.rebuild(con, key)
        return True

    def stats(self) -> dict:
        with self.connection() as con:
            total, favorites, recent, messages = con.execute("SELECT COUNT(*),COALESCE(SUM(favorite),0),COALESCE(SUM(updated_at>=?),0),COALESCE(SUM(message_count),0) FROM sessions", (time.time() - 7*86400,)).fetchone()
            providers = [dict(row) for row in con.execute("SELECT provider,COUNT(*) count,MAX(updated_at) latest FROM sessions GROUP BY provider")]
            projects = [dict(row) for row in con.execute("SELECT project,COUNT(*) count FROM sessions GROUP BY project ORDER BY count DESC,project")]
            tags = Counter()
            for row in con.execute("SELECT keywords FROM sessions"):
                tags.update(json.loads(row[0]))
            indexed_bytes = con.execute("SELECT COALESCE(SUM(offset),0) FROM source_files").fetchone()[0]
        return {"total": total, "favorites": favorites, "recent": recent, "messages": messages,
                "providers": providers, "projects": projects, "tags": [name for name, _ in tags.most_common(10)],
                "index_bytes": sum(p.stat().st_size for p in self.directory.glob("index.sqlite*") if p.is_file()), "source_bytes_read": indexed_bytes}


def excerpt(text: str, groups: list[list[str]], limit: int = 230) -> str:
    # A technical identifier may only occur inside a code block.
    readable = plain(re.sub(r"```[^\n]*\n", "", text).replace("```", ""))
    positions = [readable.lower().find(term.lower()) for group in groups for term in group if term.lower() in readable.lower()]
    at = min(positions) if positions else 0
    start = max(0, at - 70)
    return ("…" if start else "") + readable[start:start + limit] + ("…" if start + limit < len(readable) else "")


class Indexer:
    def __init__(self, store: Store, roots: dict | None = None):
        self.store = store
        self.roots = detect_sources(Layout.resolve())["roots"] if roots is None else dict(roots)
        self.status_lock = threading.Lock()
        self.running = False
        self.scan_lock = threading.Lock()
        self.header_cache: dict[str, tuple] = {}
        self.state = {"running": False, "done": 0, "total_files": 0, "last_scan": 0, "bytes_read": 0, "errors": [], "sources": []}

    def status(self) -> dict:
        with self.status_lock:
            return json.loads(json.dumps(self.state))

    def set_status(self, **values):
        with self.status_lock:
            self.state.update(values)

    def codex_metadata(self) -> dict:
        base = Path(self.roots.get("codex", "")).expanduser()
        databases = sorted(base.glob("state_*.sqlite"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not databases:
            return {}
        try:
            con = sqlite3.connect(databases[0].as_uri() + "?mode=ro", uri=True, timeout=3)
            con.row_factory = sqlite3.Row
            with con:
                columns = {r[1] for r in con.execute("PRAGMA table_info(threads)")}
                wanted = [c for c in ["id", "title", "cwd", "created_at", "updated_at", "source", "archived", "model", "rollout_path"] if c in columns]
                if "id" not in wanted:
                    return {}
                return {row["id"]: dict(row) for row in con.execute("SELECT " + ",".join(wanted) + " FROM threads") if "subagent" not in str(row["source"] if "source" in row.keys() else "")}
        except (sqlite3.Error, OSError):
            return {}
        finally:
            if "con" in locals():
                con.close()

    def codex_imports(self) -> dict:
        base = Path(self.roots.get("codex", "")).expanduser()
        try:
            records = json.loads((base / "external_agent_session_imports.json").read_text(encoding="utf-8")).get("records", [])
            return {record["imported_thread_id"]: record for record in records if isinstance(record, dict)
                    and ".claude" in Path(record.get("source_path", "")).parts and record.get("imported_thread_id")}
        except (OSError, ValueError, TypeError):
            return {}

    def claude_project_cwds(self) -> dict:
        root = Path(self.roots.get("claude", "/not-configured")).expanduser()
        result = {}
        if not self.roots.get("claude") or not root.exists():
            return result
        for path in root.glob("*/*.jsonl"):
            if path.parent.name in result:
                continue
            try:
                with path.open("rb") as file:
                    for _ in range(30):
                        line = file.readline(2 * 1024 * 1024)
                        if not line:
                            break
                        try:
                            obj = json.loads(line)
                        except (ValueError, UnicodeDecodeError):
                            continue
                        if isinstance(obj, dict) and isinstance(obj.get("cwd"), str) and obj["cwd"]:
                            result[path.parent.name] = obj["cwd"]
                            break
            except OSError:
                pass
        return result

    def header(self, path: Path) -> dict:
        stat = path.stat()
        signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
        cached = self.header_cache.get(str(path))
        if cached and cached[0] == signature:
            return cached[1]
        result = {}
        with path.open("rb") as file:
            for _ in range(5):
                line = file.readline(1024 * 1024)
                try:
                    obj = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if obj.get("type") == "session_meta":
                    result = obj.get("payload") or {}
                    break
        self.header_cache[str(path)] = (signature, result)
        return result

    def discover(self) -> tuple[list[tuple[Path, dict]], list[dict]]:
        files: list[tuple[Path, dict]] = []
        states = []
        base = Path(self.roots.get("codex", "")).expanduser()
        metadata = self.codex_metadata() if self.roots.get("codex") else {}
        imports = self.codex_imports() if self.roots.get("codex") else {}
        original_cwds = self.claude_project_cwds()
        codex_count = 0
        if self.roots.get("codex"):
            for root in [base / "sessions", base / "archived_sessions"]:
                if not root.exists():
                    continue
                for path in root.rglob("*.jsonl"):
                    try:
                        meta = self.header(path)
                        if "subagent" in str(meta.get("source", "")):
                            continue
                        native_id = meta.get("id") or meta.get("session_id")
                        if not native_id:
                            continue
                        native = metadata.get(native_id, {})
                        info = {"provider": "codex", "native_id": native_id, "format": "codex",
                            "title": native.get("title", ""), "cwd": native.get("cwd") or meta.get("cwd", ""),
                            "created_at": native.get("created_at") or meta.get("timestamp"),
                            "archived": native.get("archived", root.name == "archived_sessions"),
                            "model": native.get("model") or "", "source_kind": "local"}
                        if native_id in imports:
                            record = imports[native_id]
                            original = Path(record["source_path"])
                            claude_base = Path(self.roots.get("claude", "/not-configured")).expanduser()
                            info["imported_from"] = {"provider": "claude", "native_id": original.stem,
                                "title": record.get("title", ""), "cwd": original_cwds.get(original.parent.name, ""), "created_at": info["created_at"],
                                "source_kind": "codex-import", "mirror_thread_id": native_id,
                                "original_available": original.is_file() and original.parent.parent == claude_base}
                        files.append((path, info))
                        codex_count += 1
                    except (OSError, ValueError):
                        continue
        states.append({"provider": "codex", "label": "Codex", "path": str(base), "mode": "auto", "available": codex_count > 0, "files": codex_count,
                       "description": "自动读取桌面与 CLI 的历史会话，按原会话合并续写记录。"})
        claude_root = Path(self.roots.get("claude", "")).expanduser()
        claude_count = 0
        if self.roots.get("claude") and claude_root.exists():
            for path in claude_root.glob("*/*.jsonl"):
                if path.name.startswith("agent-"):
                    continue
                files.append((path, {"provider": "claude", "native_id": path.stem, "source_kind": "local"}))
                claude_count += 1
        states.append({"provider": "claude", "label": "Claude Code", "path": str(claude_root), "mode": "auto", "available": claude_count > 0, "files": claude_count,
                       "description": "自动读取终端主会话，提取你的问题和 AI 回复。"})
        trae_root = Path(self.roots.get("trae", "")).expanduser()
        hook_root = self.store.directory / "hooks" / "trae"
        hook_count = 0
        if hook_root.exists():
            for path in hook_root.glob("*.jsonl"):
                info = {"provider": "trae", "native_id": path.stem, "source_kind": "hook"}
                header = self.normalized_header(path)
                if not header.get("native_id"):
                    continue
                info.update(header)
                files.append((path, info))
                hook_count += 1
        configured = False
        try:
            manifest = json.loads((self.store.directory / "trae-hooks-installed.json").read_text(encoding="utf-8"))
            targets = manifest.get("configs") or [manifest]
            configured = bool(targets)
            for target in targets:
                config = json.loads(Path(target["config_path"]).read_text(encoding="utf-8"))
                configured = configured and all(any(target["helper_path"] in str(hook.get("command", ""))
                    for entry in config.get("hooks", {}).get(event, []) if isinstance(entry, dict)
                    for hook in entry.get("hooks", []) if isinstance(hook, dict)) for event in target["events"])
        except (OSError, ValueError, KeyError, TypeError):
            pass
        states.append({"provider": "trae", "label": "Trae CN", "path": str(trae_root), "mode": "hook" if configured else "import", "available": hook_count > 0,
                       "files": hook_count, "detected": bool(self.roots.get("trae") and trae_root.exists()),
                       "configured": configured, "description": "新会话通过事件收集；历史会话使用导出文件接入。" if configured else "历史会话使用导出文件接入；新会话可通过事件捕获接入。"})
        # The app writes only normalized, redacted imports to this inbox.
        inbox = self.store.directory / "imports"
        inbox.mkdir(exist_ok=True)
        for path in inbox.glob("*.jsonl"):
            info = {"provider": "other", "native_id": path.stem, "source_kind": "import"}
            header = self.normalized_header(path)
            if not header.get("native_id"):
                continue
            info.update(header)
            files.append((path, info))
        files.sort(key=lambda item: item[0].stat().st_mtime, reverse=True)
        return files, states

    @staticmethod
    def normalized_header(path: Path) -> dict:
        try:
            with path.open(encoding="utf-8") as file:
                obj = json.loads(file.readline(1024 * 1024))
            if obj.get("type") == "metadata":
                return {key: value for key, value in obj.items() if key != "type"}
        except (OSError, ValueError):
            pass
        return {}

    def parse(self, obj: dict, provider: str, info: dict, offset: int) -> tuple[list[dict], list[str]]:
        messages, models = [], []
        kind = obj.get("type")
        if info.get("format") == "codex" or (provider == "codex" and info.get("source_kind") == "local"):
            payload = obj.get("payload") or {}
            if kind == "response_item" and payload.get("type") == "message" and payload.get("role") in {"user", "assistant"} and (payload.get("phase") or payload.get("channel")) != "analysis":
                target = info.get("imported_from") if payload.get("id") is None else None
                if not (target and target.get("original_available")):
                    messages.append({"id": payload.get("id") or "snapshot:" + str(obj.get("ordinal", offset)), "role": payload["role"], "text": content_text(payload.get("content")),
                                     "timestamp": obj.get("timestamp"), "phase": payload.get("phase") or payload.get("channel") or "",
                                     "sequence": obj.get("ordinal", offset), "_target": target})
            if kind == "turn_context" and payload.get("model"):
                models.append(str(payload["model"]))
        elif info.get("source_kind") != "local":
            if kind == "metadata":
                for field in ["native_id", "provider", "title", "cwd", "project", "created_at", "updated_at", "original_url", "source_kind", "partial"]:
                    if obj.get(field) is not None:
                        info[field] = obj[field]
            elif kind == "message":
                messages.append(obj)
                if obj.get("model"):
                    models.append(str(obj["model"]))
        elif provider == "codex":
            payload = obj.get("payload") or {}
            if kind == "response_item" and payload.get("type") == "message" and payload.get("role") in {"user", "assistant"} and (payload.get("phase") or payload.get("channel")) != "analysis":
                messages.append({"id": payload.get("id"), "role": payload["role"], "text": content_text(payload.get("content")),
                                 "timestamp": obj.get("timestamp"), "phase": payload.get("phase") or payload.get("channel") or "",
                                 "sequence": obj.get("ordinal", offset)})
            if kind == "turn_context" and payload.get("model"):
                models.append(str(payload["model"]))
        elif provider == "claude" and info.get("source_kind") == "local":
            if obj.get("isSidechain"):
                return [], []
            if obj.get("cwd"):
                info["cwd"] = obj["cwd"]
            if kind in {"ai-title", "custom-title", "summary"}:
                info["title"] = obj.get("customTitle") or obj.get("aiTitle") or info.get("title") or ""
            msg = obj.get("message") or {}
            if kind in {"user", "assistant"}:
                messages.append({"id": obj.get("uuid") or msg.get("id"), "role": kind,
                                 "text": content_text(msg.get("content")), "timestamp": obj.get("timestamp"), "phase": "", "sequence": offset})
                if msg.get("model"):
                    models.append(str(msg["model"]))
        else:
            if kind == "metadata":
                for field in ["native_id", "provider", "title", "cwd", "project", "created_at", "updated_at", "original_url", "source_kind"]:
                    if obj.get(field) is not None:
                        info[field] = obj[field]
            elif kind == "message":
                messages.append(obj)
                if obj.get("model"):
                    models.append(str(obj["model"]))
        return messages, models

    def ingest_file(self, con: sqlite3.Connection, path: Path, info: dict) -> tuple[int, bool]:
        stat = path.stat()
        old = con.execute("SELECT * FROM source_files WHERE path=?", (str(path),)).fetchone()
        with path.open("rb") as file:
            prefix_hash = hashlib.sha256(file.read(min(stat.st_size, 512))).hexdigest()
            reset = bool(old and (old["inode"] != stat.st_ino or stat.st_size < old["offset"] or
                        (old["size"] >= 512 and prefix_hash != old["prefix_hash"])))
            if old and not reset and stat.st_size == old["size"] and stat.st_mtime_ns == old["mtime"]:
                # Native titles can change without a transcript append.
                existing = con.execute("SELECT original_title,archived FROM sessions WHERE key=?", (old["session_key"],)).fetchone()
                title_info = info if old["session_key"] == info["provider"] + ":" + str(info["native_id"]) else info.get("imported_from", info)
                if title_info is not info and title_info.get("original_available"):
                    return 0, False
                native_title = clean(str(title_info.get("title") or ""), "user")
                if existing and ((native_title and native_title != existing["original_title"]) or
                                 (title_info["provider"] == "codex" and int(bool(title_info.get("archived"))) != existing["archived"])):
                    key = self.store.ensure_session(con, title_info)
                    self.store.rebuild(con, key)
                return 0, False
            if reset:
                con.execute("DELETE FROM messages WHERE source_path=?", (str(path),))
            offset = 0 if reset or not old else old["offset"]
            start = offset
            file.seek(offset)
            models = [info["model"]] if info.get("model") else []
            key = self.store.ensure_session(con, info)
            touched = {key}
            changed = reset or not old
            while file.tell() < stat.st_size:
                position = file.tell()
                raw = file.readline(MAX_LINE + 1)
                if not raw.endswith(b"\n"):
                    if len(raw) > MAX_LINE:
                        while raw and not raw.endswith(b"\n"):
                            raw = file.readline(MAX_LINE + 1)
                        if not raw.endswith(b"\n"):
                            break
                        offset = file.tell()
                        continue
                    # An active client may still be writing this line.
                    break
                offset = file.tell()
                if info["provider"] == "codex" and info.get("source_kind") == "local":
                    if not any(tag in raw[:800] for tag in [b'"response_item"', b'"turn_context"']):
                        continue
                    if b'"response_item"' in raw[:200] and not re.search(rb'"type"\s*:\s*"message"', raw[:900]):
                        continue
                try:
                    obj = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    continue
                if not isinstance(obj, dict):
                    continue
                messages, new_models = self.parse(obj, info["provider"], info, position)
                models.extend(new_models)
                if obj.get("type") == "metadata" and info["source_kind"] != "local":
                    key = self.store.ensure_session(con, info)
                for msg in messages:
                    target = msg.pop("_target", None)
                    target_key = self.store.ensure_session(con, target) if target else key
                    touched.add(target_key)
                    changed |= self.store.put_message(con, target_key, msg, str(path), position)
            key = self.store.ensure_session(con, info)
            file_key = key
            if info.get("imported_from") and not con.execute("SELECT 1 FROM messages WHERE session_key=? LIMIT 1", (key,)).fetchone():
                file_key = self.store.ensure_session(con, info["imported_from"])
                touched.add(file_key)
                original = con.execute("SELECT favorite,notes FROM sessions WHERE key=?", (key,)).fetchone()
                if original and not original["favorite"] and not original["notes"]:
                    con.execute("DELETE FROM search_index WHERE session_key=?", (key,))
                    con.execute("DELETE FROM sessions WHERE key=?", (key,))
                    touched.discard(key)
            con.execute("""INSERT INTO source_files(path,provider,session_key,inode,offset,size,mtime,prefix_hash) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(path) DO UPDATE SET session_key=excluded.session_key,inode=excluded.inode,offset=excluded.offset,size=excluded.size,mtime=excluded.mtime,prefix_hash=excluded.prefix_hash""",
                (str(path), info["provider"], file_key, stat.st_ino, offset, stat.st_size, stat.st_mtime_ns, prefix_hash))
            if changed or offset > start:
                for target_key in touched:
                    if con.execute("SELECT 1 FROM sessions WHERE key=?", (target_key,)).fetchone():
                        self.store.rebuild(con, target_key, models if target_key == key else [])
            return offset - start, changed

    def scan(self):
        if not self.scan_lock.acquire(blocking=False):
            return False
        errors = []
        total_bytes = 0
        try:
            self.set_status(running=True, done=0, bytes_read=0, errors=[])
            files, sources = self.discover()
            self.set_status(total_files=len(files), sources=sources)
            con = self.store.connect()
            try:
                for index, (path, info) in enumerate(files):
                    try:
                        with con:
                            read, _ = self.ingest_file(con, path, dict(info))
                        total_bytes += read
                    except (OSError, sqlite3.Error, ValueError) as error:
                        errors.append({"file": path.name, "reason": type(error).__name__})
                    self.set_status(done=index+1, bytes_read=total_bytes, errors=errors[:20])
            finally:
                con.close()
            self.set_status(last_scan=time.time())
            return True
        except Exception as error:
            self.set_status(errors=[{"reason": type(error).__name__}])
            return False
        finally:
            self.set_status(running=False)
            self.scan_lock.release()


def normalize_provider(provider: str) -> str:
    slug = re.sub(r"\s+", "-", str(provider).strip().lower())
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", slug):
        raise ValueError("来源名称请使用 1–32 位字母、数字、下划线或连字符。")
    return slug


def normalize_import(content: str, provider: str = "other", filename: str = "导入会话") -> list[dict]:
    """Accept native ChatGPT/Claude exports and a small provider-neutral format."""
    provider = normalize_provider(provider)
    try:
        raw = json.loads(content)
    except ValueError:
        if filename.lower().endswith((".json", ".jsonl")):
            try:
                raw = [json.loads(line) for line in content.splitlines() if line.strip()]
            except ValueError:
                raise ValueError("JSON 格式不正确，请检查导出文件。")
        else:
            if not content.strip():
                raise ValueError("文件没有可索引的内容。")
            raw = {"title": Path(filename).stem, "messages": [{"role": "user", "content": content}]}
    if isinstance(raw, dict) and "sessions" in raw:
        raw = raw["sessions"]
    elif isinstance(raw, dict) and "conversations" in raw:
        raw = raw["conversations"]
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise ValueError("文件需要包含会话对象或会话数组。")
    if raw and all(isinstance(item, dict) and item.get("role") in {"user", "assistant", "human"} for item in raw):
        raw = [{"title": Path(filename).stem, "messages": raw}]
    normalized = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        detected = item.get("provider") or provider
        original_url = item.get("url") or item.get("original_url") or ""
        source_messages = item.get("messages") or item.get("chat_messages") or []
        if isinstance(item.get("mapping"), dict):
            detected = "chatgpt"
            mapping = item["mapping"]
            node = item.get("current_node")
            chain, seen = [], set()
            if not node:
                node = next((key for key, value in reversed(list(mapping.items())) if not value.get("children")), None)
            while node and node in mapping and node not in seen:
                seen.add(node)
                entry = mapping[node]
                if isinstance(entry.get("message"), dict):
                    chain.append(entry["message"])
                node = entry.get("parent")
            source_messages = list(reversed(chain))
        if "chat_messages" in item:
            detected = "claude"
        detected = normalize_provider(detected)
        messages = []
        for message_index, msg in enumerate(source_messages if isinstance(source_messages, list) else []):
            if not isinstance(msg, dict):
                continue
            role = msg.get("role") or msg.get("sender") or (msg.get("author") or {}).get("role")
            role = "user" if role == "human" else role
            if role not in {"user", "assistant"}:
                continue
            body = msg.get("text") or msg.get("content") or ""
            if isinstance(body, dict):
                body = "\n".join(part for part in body.get("parts", []) if isinstance(part, str))
            else:
                body = content_text(body)
            body = clean(body, role)
            if not body:
                continue
            messages.append({"type": "message", "id": msg.get("id") or msg.get("uuid") or str(message_index), "role": role,
                             "text": body, "timestamp": stamp(msg.get("timestamp") or msg.get("created_at") or msg.get("create_time"),
                                 stamp(item.get("created_at") or item.get("create_time"), time.time()) + message_index * 0.001),
                             "phase": msg.get("phase") or ("final" if role == "assistant" else ""), "sequence": message_index,
                             "model": (msg.get("metadata") or {}).get("model_slug") or msg.get("model") or ""})
        if not messages:
            continue
        identity = item.get("native_id") or item.get("id") or item.get("uuid") or hashlib.sha256(json.dumps(item, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
        metadata = {"type": "metadata", "provider": detected, "native_id": str(identity),
                    "title": clean(item.get("title") or item.get("name") or (Path(filename).stem + (f" · {index+1}" if len(raw)>1 else "")), "user"),
                    "cwd": str(item.get("cwd") or ""), "project": str(item.get("project") or "未关联项目"),
                    "created_at": min(m["timestamp"] for m in messages), "updated_at": max(m["timestamp"] for m in messages),
                    "original_url": redact(str(original_url)), "source_kind": "ui-mirror" if item.get("source_kind") == "ui-mirror" else "import", "partial": bool(item.get("partial"))}
        normalized.append({"metadata": metadata, "messages": messages})
    if not normalized:
        raise ValueError("未找到可识别的用户消息或 AI 回复。可导入 ChatGPT / Claude 导出 JSON，或通用 messages 格式。")
    return normalized


def save_imports(store: Store, sessions: list[dict]) -> list[Path]:
    directory = store.directory / "imports"
    directory.mkdir(exist_ok=True)
    paths = []
    for session in sessions:
        meta = session["metadata"]
        name = hashlib.sha256((meta["provider"] + ":" + meta["native_id"]).encode()).hexdigest()[:32]
        path = directory / (name + ".jsonl")
        body = "\n".join(json.dumps(obj, ensure_ascii=False) for obj in [meta] + session["messages"]) + "\n"
        if not path.exists() or path.read_text(encoding="utf-8") != body:
            temporary = path.with_suffix(".tmp")
            temporary.write_text(body, encoding="utf-8")
            os.chmod(temporary, 0o600)
            temporary.replace(path)
        paths.append(path)
    return paths
