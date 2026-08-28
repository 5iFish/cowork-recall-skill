"""WorkBuddy Desktop SQLite + projects JSONL 适配器。"""
import json
import os
from datetime import datetime
from pathlib import Path

from .common import clean_text, columns, fmt, open_ro

SOURCE = "workbuddy"


def _paths():
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    root = Path(os.environ.get("WORKSUMMARY_WORKBUDDY_DIR") or home / ".workbuddy")
    db = Path(os.environ.get("WORKSUMMARY_WORKBUDDY_DB") or root / "workbuddy.db")
    return root, db


def _num(value):
    try: return int(value or 0)
    except (TypeError, ValueError): return 0


def _texts(content):
    if isinstance(content, str): return [clean_text(content)]
    if isinstance(content, list):
        return [clean_text(x.get("text") or "") for x in content if isinstance(x, dict) and x.get("type") == "text"]
    return []


def gather(start_ts_ms, end_ts_ms, degradations):
    result = {"sessions": [], "stats_block": None, "truncated": False}
    root, db = _paths(); sessions, models = {}, {}
    if db.is_file():
        con = open_ro(str(db))
        try:
            cols = columns(con.cursor(), "sessions")
            if {"id", "created_at"} <= cols:
                selected = [x for x in ("id", "cwd", "title", "custom_title", "created_at", "updated_at", "model") if x in cols]
                for row in con.execute(f"SELECT {','.join(selected)} FROM sessions WHERE CAST(created_at AS INTEGER)>=? AND CAST(created_at AS INTEGER)<?", (start_ts_ms, end_ts_ms)):
                    data = dict(zip(selected, row)); sid = str(data["id"])
                    sessions[sid] = {"start": _num(data["created_at"]), "end": _num(data.get("updated_at")) or _num(data["created_at"]),
                        "cwd": data.get("cwd") or "", "title": data.get("custom_title") or data.get("title") or "", "prompts": []}
        finally: con.close()
    projects = root / "projects"
    for path in sorted(projects.rglob("*.jsonl")) if projects.is_dir() else []:
        sid = path.stem; data = sessions.setdefault(sid, {"start": None, "end": None, "cwd": "", "title": "", "prompts": []})
        with path.open(encoding="utf-8", errors="replace") as fh: lines = list(fh)
        for line_no, line in enumerate(lines, 1):
            try: obj = json.loads(line)
            except json.JSONDecodeError:
                degradations.append(f"[{SOURCE}] {path.name} 第 {line_no} 行损坏，已跳过"); continue
            ts = obj.get("timestamp")
            if not isinstance(ts, (int, float)) or not start_ts_ms <= int(ts) < end_ts_ms: continue
            data["start"] = min(data["start"] or int(ts), int(ts)); data["end"] = max(data["end"] or int(ts), int(ts))
            data["cwd"] = obj.get("cwd") or data["cwd"]
            if obj.get("type") == "ai-title": data["title"] = clean_text(obj.get("aiTitle") or "") or data["title"]
            if obj.get("type") == "message" and obj.get("role") == "user":
                for text in _texts(obj.get("content")):
                    if len(text) >= 4 and text not in data["prompts"]: data["prompts"].append(text)
            if obj.get("type") in ("message", "function_call"):
                provider = obj.get("providerData") or {}; usage = provider.get("usage") or {}
                if usage:
                    model_id = provider.get("model") or provider.get("requestModelId") or "unknown"
                    model = models.setdefault(model_id, {"model_id": model_id, "requests": 0, "in": 0, "out": 0,
                        "cache_read": 0, "reasoning": 0, "total": 0, "tool_calls": 0, "duration_ms": None})
                    inp, out = _num(usage.get("inputTokens")), _num(usage.get("outputTokens"))
                    cache = sum(_num(x.get("cached_tokens")) for x in usage.get("inputTokensDetails") or [] if isinstance(x, dict))
                    reasoning = sum(_num(x.get("reasoning_tokens")) for x in usage.get("outputTokensDetails") or [] if isinstance(x, dict))
                    model["requests"] += _num(usage.get("requests")) or 1; model["in"] += inp; model["out"] += out
                    model["cache_read"] += cache; model["reasoning"] += reasoning
                    model["total"] += _num(usage.get("totalTokens")) or inp + out + reasoning
                    if obj.get("type") == "function_call": model["tool_calls"] += 1
    for sid, data in sessions.items():
        if data["start"] is None or not (start_ts_ms <= data["start"] < end_ts_ms): continue
        local = datetime.fromtimestamp(data["start"] / 1000).astimezone()
        result["sessions"].append({"source": SOURCE, "variant": "workbuddy_desktop", "backend": "workbuddy_sqlite_jsonl",
            "parent_session_id": None, "is_subagent": False, "session_id": sid, "day": local.strftime("%Y-%m-%d"),
            "start": local.strftime("%H:%M"), "end": datetime.fromtimestamp((data["end"] or data["start"]) / 1000).astimezone().strftime("%H:%M"),
            "_order": local.strftime("%Y-%m-%d%H:%M"), "title": data["title"] or (data["prompts"][0][:60] if data["prompts"] else ""),
            "dir": data["cwd"], "prompts": data["prompts"][:6]})
    rows = sorted(models.values(), key=lambda m: -m["total"])
    for model in rows: model.update(in_h=fmt(model["in"]), out_h=fmt(model["out"]), total_h=fmt(model["total"]))
    result["stats_block"] = {"models": rows, "grand_total": sum(m["total"] for m in rows), "nonmain_total": 0}
    return result
