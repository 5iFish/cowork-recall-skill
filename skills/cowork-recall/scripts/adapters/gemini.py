"""Gemini CLI JSONL 会话适配器。"""
import json
import os
from datetime import datetime
from pathlib import Path

from .common import PROMPT_CAP, clean_text, fmt

SOURCE = "gemini"


def root_path():
    env = os.environ.get("WORKSUMMARY_GEMINI_DIR")
    if env:
        return Path(env)
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return Path(home) / ".gemini" / "tmp"


def _ms(value):
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _num(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _content_text(value):
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, list):
        return clean_text(" ".join(
            str(item.get("text") or item.get("content") or "")
            for item in value if isinstance(item, dict)))
    return ""


def gather(start_ts_ms, end_ts_ms, degradations):
    root = root_path()
    result = {"sessions": [], "stats_block": None, "truncated": False}
    if not root.is_dir():
        degradations.append(f"[{SOURCE}] 目录不存在: {root}")
        return result
    groups, models = {}, {}
    for path in sorted(root.rglob("session-*.jsonl")):
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line_no, line in enumerate(fh, 1):
                try:
                    obj = json.loads(line)
                    if not isinstance(obj, dict):
                        raise ValueError
                except (json.JSONDecodeError, ValueError):
                    degradations.append(f"[{SOURCE}] {path.name} 第 {line_no} 行损坏，已跳过")
                    continue
                sid = obj.get("sessionId") or path.stem.removeprefix("session-")
                ts = _ms(obj.get("timestamp") or obj.get("startTime") or obj.get("lastUpdated"))
                group = groups.setdefault(sid, {"start": None, "end": None, "cwd": "", "prompts": []})
                directories = obj.get("directories")
                if isinstance(directories, list) and directories:
                    group["cwd"] = directories[0]
                if ts is not None and start_ts_ms <= ts < end_ts_ms:
                    group["start"] = min(group["start"] or ts, ts)
                    group["end"] = max(group["end"] or ts, ts)
                    if obj.get("type") == "user":
                        text = _content_text(obj.get("displayContent") or obj.get("content") or "")
                        if len(text) >= 4 and text not in group["prompts"] and len(group["prompts"]) < PROMPT_CAP:
                            group["prompts"].append(text)
                    tokens = obj.get("tokens")
                    if isinstance(tokens, dict):
                        model_id = obj.get("model") or "unknown"
                        model = models.setdefault(model_id, {"model_id": model_id, "requests": 0,
                            "in": 0, "out": 0, "cache_read": 0, "reasoning": 0,
                            "total": 0, "tool_calls": 0, "duration_ms": None})
                        inp, out, reasoning = _num(tokens.get("input")), _num(tokens.get("output")), _num(tokens.get("thoughts"))
                        model["requests"] += 1
                        model["in"] += inp
                        model["out"] += out
                        model["cache_read"] += _num(tokens.get("cached"))
                        model["reasoning"] += reasoning
                        model["total"] += _num(tokens.get("total")) or inp + out + reasoning
                        calls = obj.get("toolCalls")
                        model["tool_calls"] += len(calls) if isinstance(calls, list) else 0
    sessions = []
    for sid, group in groups.items():
        if group["start"] is None or not group["prompts"]:
            continue
        start = datetime.fromtimestamp(group["start"] / 1000).astimezone()
        end = datetime.fromtimestamp((group["end"] or group["start"]) / 1000).astimezone()
        sessions.append({"source": SOURCE, "variant": "gemini_cli", "backend": "gemini_cli_jsonl",
            "parent_session_id": None, "is_subagent": False, "session_id": sid,
            "day": start.strftime("%Y-%m-%d"), "start": start.strftime("%H:%M"),
            "end": end.strftime("%H:%M"), "_order": start.strftime("%Y-%m-%d%H:%M"),
            "title": group["prompts"][0][:60], "dir": group["cwd"], "prompts": group["prompts"]})
    model_rows = sorted(models.values(), key=lambda item: -item["total"])
    for model in model_rows:
        model.update(in_h=fmt(model["in"]), out_h=fmt(model["out"]), total_h=fmt(model["total"]))
    result["sessions"] = sessions
    result["stats_block"] = {"models": model_rows,
        "grand_total": sum(m["total"] for m in model_rows), "nonmain_total": 0}
    return result
