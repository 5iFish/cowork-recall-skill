"""Continue Core sessions 适配器。"""
import json
import os
from datetime import datetime
from pathlib import Path

from .common import PROMPT_CAP, clean_text, fmt

SOURCE = "continue"


def root_path():
    env = os.environ.get("WORKSUMMARY_CONTINUE_DIR")
    if env:
        return Path(env)
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return Path(home) / ".continue"


def _num(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _text(message):
    content = message.get("content") if isinstance(message, dict) else ""
    if isinstance(content, str):
        return clean_text(content)
    if isinstance(content, list):
        return clean_text(" ".join(str(x.get("text") or "") for x in content if isinstance(x, dict)))
    return ""


def gather(start_ts_ms, end_ts_ms, degradations):
    result = {"sessions": [], "stats_block": None, "truncated": False}
    sessions_dir = root_path() / "sessions"
    if not sessions_dir.is_dir():
        degradations.append(f"[{SOURCE}] 目录不存在: {sessions_dir}")
        return result
    models = {}
    index_by_id = {}
    index_path = sessions_dir / "sessions.json"
    try:
        index_rows = json.loads(index_path.read_text(encoding="utf-8"))
        if isinstance(index_rows, list):
            index_by_id = {str(row.get("sessionId")): row for row in index_rows if isinstance(row, dict)}
    except (OSError, json.JSONDecodeError):
        pass
    for path in sorted(sessions_dir.glob("*.json")):
        if path.name == "sessions.json":
            continue
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            degradations.append(f"[{SOURCE}] 会话文件不可读: {path.name}: {exc}")
            continue
        sid = str(obj.get("sessionId") or path.stem)
        index_row = index_by_id.get(sid) or {}
        created = _num(index_row.get("dateCreated"))
        prompts, timestamps = [], []
        for item in obj.get("history") or []:
            if not isinstance(item, dict):
                continue
            ts = _num(item.get("timestamp")) or created
            if start_ts_ms <= ts < end_ts_ms:
                timestamps.append(ts)
                message = item.get("message") or {}
                if message.get("role") == "user":
                    text = _text(message)
                    if len(text) >= 4 and text not in prompts and len(prompts) < PROMPT_CAP:
                        prompts.append(text)
                logs = item.get("promptLogs") or ([item.get("promptLog")] if item.get("promptLog") else [])
                for log in logs:
                    if not isinstance(log, dict):
                        continue
                    model_id = log.get("modelTitle") or "unknown"
                    model = models.setdefault(model_id, {"model_id": model_id, "requests": 0,
                        "in": 0, "out": 0, "cache_read": 0, "reasoning": 0,
                        "total": 0, "tool_calls": None, "duration_ms": None})
                    inp, out = _num(log.get("promptTokens")), _num(log.get("completionTokens"))
                    model["requests"] += 1
                    model["in"] += inp
                    model["out"] += out
                    model["total"] += inp + out
        if not timestamps or not prompts:
            continue
        start, end = min(timestamps), max(timestamps)
        local = datetime.fromtimestamp(start / 1000).astimezone()
        result["sessions"].append({"source": SOURCE, "variant": "continue_core",
            "backend": "continue_sessions_json", "parent_session_id": None, "is_subagent": False,
            "session_id": sid, "day": local.strftime("%Y-%m-%d"), "start": local.strftime("%H:%M"),
            "end": datetime.fromtimestamp(end / 1000).astimezone().strftime("%H:%M"),
            "_order": local.strftime("%Y-%m-%d%H:%M"), "title": obj.get("title") or prompts[0][:60],
            "dir": obj.get("workspaceDirectory") or "", "prompts": prompts})
    rows = sorted(models.values(), key=lambda m: -m["total"])
    for model in rows:
        model.update(in_h=fmt(model["in"]), out_h=fmt(model["out"]), total_h=fmt(model["total"]))
    result["stats_block"] = {"models": rows, "grand_total": sum(m["total"] for m in rows), "nonmain_total": 0}
    return result
