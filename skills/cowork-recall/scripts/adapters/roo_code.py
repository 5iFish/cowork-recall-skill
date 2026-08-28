"""Roo Code task storage 适配器。"""
import json
import os
from datetime import datetime
from pathlib import Path

from .common import clean_text, fmt

SOURCE = "roo_code"


def root_path():
    env = os.environ.get("WORKSUMMARY_ROO_DIR")
    if env:
        return Path(env)
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    return appdata / "Code" / "User" / "globalStorage" / "rooveterinaryinc.roo-cline"


def _num(value):
    try: return int(value or 0)
    except (TypeError, ValueError): return 0


def gather(start_ts_ms, end_ts_ms, degradations):
    result = {"sessions": [], "stats_block": None, "truncated": False}
    tasks = root_path() / "tasks"
    index = tasks / "_index.json"
    if not index.is_file():
        degradations.append(f"[{SOURCE}] 任务索引不存在: {index}")
        return result
    try:
        rows = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        degradations.append(f"[{SOURCE}] 任务索引不可读: {exc}")
        return result
    models = {}
    if isinstance(rows, dict):
        rows = rows.get("entries") or rows.get("tasks") or rows.get("items") or rows.get("history") or []
    for row in rows if isinstance(rows, list) else []:
        sid = str(row.get("id") or row.get("taskId") or "")
        item_dir = tasks / sid
        try: meta = json.loads((item_dir / "history_item.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): meta = row
        start = meta.get("ts") or meta.get("timestamp") or row.get("ts")
        if not isinstance(start, (int, float)) or not start_ts_ms <= int(start) < end_ts_ms:
            continue
        try: history = json.loads((item_dir / "api_conversation_history.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): history = []
        prompts, end = [], int(start)
        for message in history if isinstance(history, list) else []:
            ts = message.get("ts") or message.get("timestamp") or start
            if isinstance(ts, (int, float)): end = max(end, int(ts))
            if message.get("role") == "user":
                text = clean_text(message.get("content") if isinstance(message.get("content"), str) else "")
                if len(text) >= 4 and text not in prompts: prompts.append(text)
            if message.get("role") == "assistant" and isinstance(message.get("usage"), dict):
                usage, model_id = message["usage"], message.get("model") or "unknown"
                model = models.setdefault(model_id, {"model_id": model_id, "requests": 0,
                    "in": 0, "out": 0, "cache_read": 0, "reasoning": 0, "total": 0,
                    "tool_calls": 0, "duration_ms": None})
                inp, out = _num(usage.get("input_tokens")), _num(usage.get("output_tokens"))
                reasoning = _num(usage.get("reasoning_tokens"))
                model["requests"] += 1; model["in"] += inp; model["out"] += out
                model["cache_read"] += _num(usage.get("cache_read_input_tokens"))
                model["reasoning"] += reasoning; model["total"] += inp + out + reasoning
                content = message.get("content") or []
                model["tool_calls"] += sum(1 for x in content if isinstance(x, dict)
                                           and x.get("type") in ("tool_use", "function_call"))
        local = datetime.fromtimestamp(int(start) / 1000).astimezone()
        result["sessions"].append({"source": SOURCE, "variant": "roo_vscode", "backend": "roo_tasks_json",
            "parent_session_id": None, "is_subagent": False, "session_id": sid,
            "day": local.strftime("%Y-%m-%d"), "start": local.strftime("%H:%M"),
            "end": datetime.fromtimestamp(end / 1000).astimezone().strftime("%H:%M"),
            "_order": local.strftime("%Y-%m-%d%H:%M"), "title": meta.get("task") or (prompts[0][:60] if prompts else ""),
            "dir": meta.get("workspace") or meta.get("cwd") or "", "prompts": prompts[:6]})
    rows_out = sorted(models.values(), key=lambda m: -m["total"])
    for model in rows_out: model.update(in_h=fmt(model["in"]), out_h=fmt(model["out"]), total_h=fmt(model["total"]))
    result["stats_block"] = {"models": rows_out, "grand_total": sum(m["total"] for m in rows_out), "nonmain_total": 0}
    return result
