"""Kimi Code wire JSONL 适配器。"""
import json
import os
from datetime import datetime
from pathlib import Path

from .common import PROMPT_CAP, clean_text, fmt

SOURCE = "kimi_code"


def root_path():
    env = os.environ.get("WORKSUMMARY_KIMI_DIR")
    if env:
        return Path(env)
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return Path(home) / ".kimi-code"


def _num(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _texts(content):
    if isinstance(content, str):
        return [clean_text(content)]
    if isinstance(content, list):
        return [clean_text(item.get("text") or "") for item in content
                if isinstance(item, dict) and item.get("type") == "text"]
    return []


def gather(start_ts_ms, end_ts_ms, degradations):
    result = {"sessions": [], "stats_block": None, "truncated": False}
    root = root_path()
    index = root / "session_index.jsonl"
    if not index.is_file():
        degradations.append(f"[{SOURCE}] 会话索引不存在: {index}")
        return result
    entries = []
    with index.open(encoding="utf-8", errors="replace") as fh:
        index_lines = list(fh)
    for line in index_lines:
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                entries.append(obj)
        except json.JSONDecodeError:
            degradations.append(f"[{SOURCE}] session_index.jsonl 存在损坏行，已跳过")
    models, nonmain = {}, 0
    for entry in entries:
        session_dir = Path(entry.get("sessionDir") or "")
        state_path = session_dir / "state.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            degradations.append(f"[{SOURCE}] state.json 不可读: {exc}")
            continue
        created, updated = _num(state.get("createdAt")), _num(state.get("updatedAt"))
        if not (created < end_ts_ms and (updated or created) >= start_ts_ms):
            continue
        prompts, event_times = [], []
        for wire in sorted((session_dir / "agents").glob("*/wire.jsonl")):
            agent_id = wire.parent.name
            current_model = "unknown"
            last_usage_model = None
            with wire.open(encoding="utf-8", errors="replace") as fh:
                wire_lines = list(fh)
            for line_no, line in enumerate(wire_lines, 1):
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    degradations.append(f"[{SOURCE}] {wire.name} 第 {line_no} 行损坏，已跳过")
                    continue
                ts = obj.get("time")
                if isinstance(ts, (int, float)) and start_ts_ms <= int(ts) < end_ts_ms:
                    event_times.append(int(ts))
                typ = obj.get("type")
                if typ == "prompt.accepted" and agent_id == "main":
                    for text in _texts(obj.get("content")):
                        if len(text) >= 4 and text not in prompts and len(prompts) < PROMPT_CAP:
                            prompts.append(text)
                if typ in ("profile.bind", "llm.request"):
                    current_model = obj.get("modelAlias") or obj.get("model") or current_model
                if typ == "usage.record" and isinstance(ts, (int, float)) and start_ts_ms <= int(ts) < end_ts_ms:
                    model_id = obj.get("model") or current_model
                    usage = obj.get("usage") or {}
                    model = models.setdefault(model_id, {"model_id": model_id, "requests": 0,
                        "in": 0, "out": 0, "cache_read": 0, "reasoning": 0,
                        "total": 0, "tool_calls": 0, "duration_ms": 0})
                    inp, out = _num(usage.get("inputOther")), _num(usage.get("output"))
                    total = inp + out
                    model["requests"] += 1
                    model["in"] += inp
                    model["out"] += out
                    model["cache_read"] += _num(usage.get("inputCacheRead"))
                    model["total"] += total
                    last_usage_model = model_id
                    if agent_id != "main":
                        nonmain += total
                if typ == "turn.ended" and agent_id == "main":
                    model = models.get(last_usage_model or current_model)
                    if model is not None:
                        model["duration_ms"] += _num(obj.get("durationMs"))
        if not event_times and not (start_ts_ms <= created < end_ts_ms):
            continue
        start = max(start_ts_ms, min(event_times or [created]))
        end = min(end_ts_ms - 1, max(event_times or [updated or created]))
        local = datetime.fromtimestamp(start / 1000).astimezone()
        result["sessions"].append({"source": SOURCE, "variant": "kimi_code_cli",
            "backend": "kimi_code_wire_jsonl", "parent_session_id": None, "is_subagent": False,
            "session_id": state.get("id") or entry.get("sessionId") or session_dir.name,
            "day": local.strftime("%Y-%m-%d"), "start": local.strftime("%H:%M"),
            "end": datetime.fromtimestamp(end / 1000).astimezone().strftime("%H:%M"),
            "_order": local.strftime("%Y-%m-%d%H:%M"),
            "title": state.get("title") or (prompts[0][:60] if prompts else ""),
            "dir": state.get("cwd") or entry.get("workDir") or "", "prompts": prompts})
    rows = sorted(models.values(), key=lambda m: -m["total"])
    for model in rows:
        model.update(in_h=fmt(model["in"]), out_h=fmt(model["out"]), total_h=fmt(model["total"]))
    result["stats_block"] = {"models": rows, "grand_total": sum(m["total"] for m in rows),
                             "nonmain_total": nonmain}
    return result
