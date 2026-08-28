"""Qoder 产品族适配器；完整读取 Desktop/CN Desktop，IDE 仅安全探测。"""
import json
import os
from datetime import datetime
from pathlib import Path

from .common import clean_text, columns, fmt, open_ro, table_names

SOURCE = "qoder"


def _num(value):
    try: return int(value or 0)
    except (TypeError, ValueError): return 0


def _desktop_specs():
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    explicit = os.environ.get("WORKSUMMARY_QODER_DIR")
    if explicit:
        root = Path(explicit)
        return [("qoder_desktop", root, Path(os.environ.get("WORKSUMMARY_QODER_DB") or root / "main.sqlite"))]
    return [
        ("qoder_desktop", home / ".qoder", appdata / "com.qoder.app.stable" / "main.sqlite"),
        ("qoder_cn_desktop", home / ".qoder-cn", appdata / "com.qodercn.app.stable" / "main.sqlite"),
    ]


def _message_text(obj):
    text = obj.get("text")
    if isinstance(text, str): return clean_text(text)
    message = obj.get("message") or {}
    if isinstance(message, dict):
        content = message.get("content") or message.get("text")
        if isinstance(content, str): return clean_text(content)
    return ""


def _usage(snapshot):
    api = snapshot.get("apiUsage") or {}
    inp = _num(api.get("inputTokens") or api.get("input_tokens"))
    out = _num(api.get("outputTokens") or api.get("output_tokens"))
    cache = _num(api.get("cacheReadTokens") or api.get("cache_read_tokens"))
    reasoning = _num(api.get("reasoningTokens") or api.get("reasoning_tokens"))
    total = _num(snapshot.get("totalTokens")) or _num(api.get("totalTokens")) or inp + out + reasoning
    return inp, out, cache, reasoning, total


def gather(start_ts_ms, end_ts_ms, degradations):
    result = {"sessions": [], "stats_block": None, "truncated": False}
    sessions, models = {}, {}
    for variant, root, db in _desktop_specs():
        prefix = f"{variant}:"
        if db.is_file():
            con = open_ro(str(db))
            try:
                tables = table_names(con.cursor()); cols = columns(con.cursor(), "chat_sessions")
                if "chat_sessions" in tables and {"session_id", "created_at"} <= cols:
                    optional = [x for x in ("title", "cwd", "model", "updated_at", "origin_session_id", "owner_session_id", "session_kind") if x in cols]
                    selected = ["session_id", "created_at", *optional]
                    for row in con.execute(f"SELECT {','.join(selected)} FROM chat_sessions WHERE CAST(created_at AS INTEGER)>=? AND CAST(created_at AS INTEGER)<?", (start_ts_ms, end_ts_ms)):
                        data = dict(zip(selected, row)); sid = str(data["session_id"]); key = prefix + sid
                        item = sessions.setdefault(key, {"sid": sid, "variant": variant, "start": _num(data["created_at"]),
                            "end": _num(data.get("updated_at")) or _num(data["created_at"]), "cwd": data.get("cwd") or "",
                            "title": data.get("title") or "", "model": data.get("model") or "unknown", "prompts": []})
                        if "chat_session_messages" in tables:
                            for (raw,) in con.execute("SELECT payload_json FROM chat_session_messages WHERE session_id=? ORDER BY sequence", (sid,)):
                                try: obj = json.loads(raw)
                                except (TypeError, json.JSONDecodeError): continue
                                if obj.get("role") == "user":
                                    text = _message_text(obj)
                                    if len(text) >= 4 and text not in item["prompts"]: item["prompts"].append(text)
                        if "chat_session_context_usage" in tables:
                            rows = con.execute("SELECT snapshot_json FROM chat_session_context_usage WHERE session_id=? ORDER BY updated_at DESC LIMIT 1", (sid,)).fetchall()
                            if rows:
                                try: snapshot = json.loads(rows[0][0])
                                except (TypeError, json.JSONDecodeError): snapshot = {}
                                inp, out, cache, reasoning, total = _usage(snapshot)
                                if total:
                                    model_id = snapshot.get("model") or item["model"]
                                    models[prefix + model_id] = {"model_id": model_id, "requests": 1, "in": inp, "out": out,
                                        "cache_read": cache, "reasoning": reasoning, "total": total,
                                        "tool_calls": None, "duration_ms": None}
            finally: con.close()
        projects = root / "projects"
        for path in sorted(projects.rglob("*.jsonl")) if projects.is_dir() else []:
            sid = path.stem; key = prefix + sid
            with path.open(encoding="utf-8", errors="replace") as fh: lines = list(fh)
            for line in lines:
                try: obj = json.loads(line)
                except json.JSONDecodeError: continue
                if obj.get("type") == "workspace-directories":
                    dirs = obj.get("directories") or []
                    sessions.setdefault(key, {"sid": sid, "variant": variant, "start": None, "end": None,
                        "cwd": dirs[0] if dirs else "", "title": "", "model": "unknown", "prompts": []})
                    continue
                ts = obj.get("timestamp")
                if not isinstance(ts, (int, float)) or not start_ts_ms <= int(ts) < end_ts_ms: continue
                item = sessions.setdefault(key, {"sid": sid, "variant": variant, "start": int(ts), "end": int(ts),
                    "cwd": "", "title": "", "model": "unknown", "prompts": []})
                item["start"] = min(item["start"] or int(ts), int(ts)); item["end"] = max(item["end"] or int(ts), int(ts))
                item["cwd"] = obj.get("cwd") or item["cwd"]
                if obj.get("type") == "runtime-config": item["model"] = obj.get("model") or item["model"]
                if obj.get("type") == "user" and not obj.get("isSidechain"):
                    text = _message_text(obj)
                    if len(text) >= 4 and text not in item["prompts"]: item["prompts"].append(text)
    for item in sessions.values():
        if item["start"] is None or not start_ts_ms <= item["start"] < end_ts_ms: continue
        local = datetime.fromtimestamp(item["start"] / 1000).astimezone()
        result["sessions"].append({"source": SOURCE, "variant": item["variant"], "backend": f"{item['variant']}_sqlite_jsonl",
            "parent_session_id": None, "is_subagent": False, "session_id": item["sid"], "day": local.strftime("%Y-%m-%d"),
            "start": local.strftime("%H:%M"), "end": datetime.fromtimestamp((item["end"] or item["start"]) / 1000).astimezone().strftime("%H:%M"),
            "_order": local.strftime("%Y-%m-%d%H:%M"), "title": item["title"] or (item["prompts"][0][:60] if item["prompts"] else ""),
            "dir": item["cwd"], "prompts": item["prompts"][:6]})
    rows = sorted(models.values(), key=lambda m: -m["total"])
    for model in rows: model.update(in_h=fmt(model["in"]), out_h=fmt(model["out"]), total_h=fmt(model["total"]))
    result["stats_block"] = {"models": rows, "grand_total": sum(m["total"] for m in rows), "nonmain_total": 0}
    return result
