#!/usr/bin/env python3
"""Capture Trae's conversation events without changing its replies."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

from .core import clean, content_text
from .portable import locked_file, read_json, write_json, private_directory


def text_value(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return text_value(value.get("text") or value.get("content") or value.get("message"))
    if isinstance(value, list):
        return content_text(value)
    return ""


def capture(payload: dict, event: str, directory: Path) -> dict:
    identity = payload.get("session_id") or payload.get("sessionId") or payload.get("conversation_id") or payload.get("task_id")
    when = time.time()
    status = {"last_event": event, "timestamp": when, "session_identified": bool(identity), "message_captured": False,
              "input_fields": sorted(str(key) for key in payload.keys())[:60]}
    private_directory(directory)
    if not identity or not isinstance(identity, (str, int)):
        return status
    native_id = str(identity)
    if len(native_id) > 300:
        return status
    cwd = payload.get("cwd") or payload.get("workspace_dir") or ""
    if not cwd and isinstance(payload.get("workspace_roots"), list) and payload["workspace_roots"]:
        root = payload["workspace_roots"][0]
        cwd = root if isinstance(root, str) else root.get("path", "") if isinstance(root, dict) else ""
    title = clean(text_value(payload.get("title") or payload.get("session_title")), "user")
    metadata = {"type": "metadata", "provider": "trae", "native_id": native_id, "cwd": str(cwd), "source_kind": "hook"}
    if title:
        metadata["title"] = title
    if event == "SessionStart":
        metadata["created_at"] = when
    body, role = "", ""
    if event == "UserPromptSubmit":
        body = text_value(payload.get("prompt") or payload.get("text_content") or payload.get("user_prompt") or payload.get("user_input"))
        role = "user"
    elif event == "Stop":
        body = text_value(payload.get("last_assistant_message") or payload.get("assistant_message"))
        role = "assistant"
    body = clean(body, role)
    records = [metadata]
    if body:
        records.append({"type": "message", "role": role, "text": body, "timestamp": when,
                        "phase": "final" if role == "assistant" else "",
                        "id": str(payload.get("event_id") or payload.get("message_id") or payload.get("prompt_id") or uuid.uuid4()),
                        "model": str(payload.get("model") or payload.get("model_name") or "")})
        status["message_captured"] = True
    destination = directory / "hooks" / "trae"
    destination.mkdir(parents=True, exist_ok=True)
    filename = hashlib.sha256(native_id.encode()).hexdigest()[:32] + ".jsonl"
    path = destination / filename
    with path.open("a+", encoding="utf-8") as file, locked_file(file):
        file.write("\n".join(json.dumps(row, ensure_ascii=False) for row in records) + "\n")
        file.flush()
    if os.name != "nt":
        os.chmod(path, 0o600)
    return status


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--event", required=True, choices=["SessionStart", "UserPromptSubmit", "Stop"])
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--capture-id", default="")
    parser.add_argument("--client", default="Trae")
    args = parser.parse_args()
    try:
        raw = sys.stdin.buffer.read(8 * 1024 * 1024)
        payload = json.loads(raw or b"{}")
        if not isinstance(payload, dict):
            return
        directory = Path(args.data_dir).expanduser()
        status = capture(payload, args.event, directory)
        status["client"] = args.client
        status["capture_id"] = args.capture_id
        with (directory / ".capture-status.lock").open("a+") as lock, locked_file(lock):
            previous = read_json(directory / "trae-capture-status.json") if (directory / "trae-capture-status.json").is_file() else {}
            if previous.get("capture_id") != args.capture_id:
                previous = {}
            status["observed_message_count"] = int(previous.get("observed_message_count", 0)) + int(status["message_captured"])
            status["last_message_at"] = status["timestamp"] if status["message_captured"] else previous.get("last_message_at", 0)
            write_json(directory / "trae-capture-status.json", status)
    except Exception:
        return


if __name__ == "__main__":
    main()
