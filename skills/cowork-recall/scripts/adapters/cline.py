"""Cline Shared Core SQLite 与旧版 tasks 适配器。"""
import json
import os
from datetime import datetime
from pathlib import Path

from .common import clean_text, columns, open_ro

SOURCE = "cline"


def _ms(value):
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _paths():
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    root = Path(os.environ.get("WORKSUMMARY_CLINE_DIR") or home / ".cline")
    db = Path(os.environ.get("WORKSUMMARY_CLINE_DB") or root / "data" / "db" / "sessions.db")
    legacy = Path(os.environ.get("WORKSUMMARY_CLINE_LEGACY_DIR") or
                  appdata / "Code" / "User" / "globalStorage" / "saoudrizwan.claude-dev")
    return db, legacy


def _legacy(legacy, start_ms, end_ms, degradations):
    found = {}
    index = legacy / "state" / "taskHistory.json"
    if not index.is_file():
        return found
    try:
        rows = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        degradations.append(f"[{SOURCE}] legacy taskHistory 不可读: {exc}")
        return found
    for row in rows if isinstance(rows, list) else []:
        sid = str(row.get("id") or row.get("taskId") or "")
        ts = row.get("ts") or row.get("timestamp")
        if not sid or not isinstance(ts, (int, float)) or not start_ms <= int(ts) < end_ms:
            continue
        prompts = []
        history = legacy / "tasks" / sid / "api_conversation_history.json"
        try:
            items = json.loads(history.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            items = []
        for item in items if isinstance(items, list) else []:
            if item.get("role") != "user":
                continue
            content = item.get("content")
            text = clean_text(content if isinstance(content, str) else "")
            if len(text) >= 4 and text not in prompts:
                prompts.append(text)
        found[sid] = {"ts": int(ts), "title": clean_text(row.get("task") or ""), "prompts": prompts}
    return found


def gather(start_ts_ms, end_ts_ms, degradations):
    result = {"sessions": [], "stats_block": None, "truncated": False}
    db, legacy_root = _paths()
    legacy = _legacy(legacy_root, start_ts_ms, end_ts_ms, degradations)
    current = {}
    if db.is_file():
        con = open_ro(str(db))
        try:
            cols = columns(con.cursor(), "sessions")
            required = {"session_id", "started_at"}
            if not required <= cols:
                degradations.append(f"[{SOURCE}] sessions schema 不兼容")
            else:
                wanted = [name for name in ("session_id", "started_at", "ended_at", "cwd",
                    "workspace_root", "parent_session_id", "is_subagent", "prompt") if name in cols]
                sql = f"SELECT {','.join(wanted)} FROM sessions"
                for row in con.execute(sql):
                    data = dict(zip(wanted, row))
                    started = _ms(data.get("started_at"))
                    if started is None or not start_ts_ms <= started < end_ts_ms:
                        continue
                    sid = str(data["session_id"])
                    prompt = clean_text(data.get("prompt") or "")
                    current[sid] = {"start": started,
                        "end": _ms(data.get("ended_at")) or started,
                        "cwd": data.get("cwd") or data.get("workspace_root") or "",
                        "parent": data.get("parent_session_id"),
                        "sub": bool(data.get("is_subagent")),
                        "prompts": [prompt] if len(prompt) >= 4 else []}
        finally:
            con.close()
    elif not legacy:
        degradations.append(f"[{SOURCE}] 当前数据库和 legacy 目录均不可用")
    merged_ids = list(dict.fromkeys([*current, *legacy]))
    for sid in merged_ids:
        cur, old = current.get(sid), legacy.get(sid)
        if cur:
            prompts = list(cur["prompts"])
            for text in (old or {}).get("prompts", []):
                if text not in prompts:
                    prompts.append(text)
            start_ms, end_ms = cur["start"], cur["end"]
            variant, backend = "cline_vscode", "cline_core_sqlite"
            cwd, parent, sub = cur["cwd"], cur["parent"], cur["sub"]
        else:
            prompts = old["prompts"]
            start_ms = end_ms = old["ts"]
            variant, backend = "cline_vscode_legacy", "cline_legacy_tasks_json"
            cwd, parent, sub = "", None, False
        local = datetime.fromtimestamp(start_ms / 1000).astimezone()
        result["sessions"].append({"source": SOURCE, "variant": variant, "backend": backend,
            "parent_session_id": parent, "is_subagent": sub, "session_id": sid,
            "day": local.strftime("%Y-%m-%d"), "start": local.strftime("%H:%M"),
            "end": datetime.fromtimestamp(end_ms / 1000).astimezone().strftime("%H:%M"),
            "_order": local.strftime("%Y-%m-%d%H:%M"),
            "title": ((old or {}).get("title") or (prompts[0][:60] if prompts else "")),
            "dir": cwd, "prompts": prompts[:6]})
    return result
