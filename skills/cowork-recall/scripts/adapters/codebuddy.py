"""CodeBuddy CLI projects JSONL 适配器。"""
import json
import os
from datetime import datetime
from pathlib import Path

from .common import PROMPT_CAP, clean_text, fmt

SOURCE = "codebuddy"


def root_path():
    env = os.environ.get("WORKSUMMARY_CODEBUDDY_DIR")
    if env:
        return Path(env)
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return Path(home) / ".codebuddy" / "projects"


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
                if isinstance(item, dict) and item.get("type") in ("text", "input_text")]
    return []


def gather(start_ts_ms, end_ts_ms, degradations):
    result = {"sessions": [], "stats_block": None, "truncated": False}
    root = root_path()
    if not root.is_dir():
        degradations.append(f"[{SOURCE}] 目录不存在: {root}")
        return result
    models = {}
    for path in sorted(root.rglob("*.jsonl")):
        prompts, times, title, cwd, sid = [], [], "", "", path.stem
        with path.open(encoding="utf-8", errors="replace") as fh:
            lines = list(fh)
        for line_no, line in enumerate(lines, 1):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                degradations.append(f"[{SOURCE}] {path.name} 第 {line_no} 行损坏，已跳过")
                continue
            ts = obj.get("timestamp")
            if not isinstance(ts, (int, float)) or not start_ts_ms <= int(ts) < end_ts_ms:
                continue
            times.append(int(ts))
            sid = obj.get("sessionId") or sid
            cwd = obj.get("cwd") or cwd
            if obj.get("type") == "ai-title":
                title = clean_text(obj.get("aiTitle") or "")
            if obj.get("type") == "message" and obj.get("role") == "user":
                for text in _texts(obj.get("content")):
                    if len(text) >= 4 and text not in prompts and len(prompts) < PROMPT_CAP:
                        prompts.append(text)
            if obj.get("type") == "message" and obj.get("role") == "assistant":
                provider = obj.get("providerData") or {}
                usage = provider.get("usage") or {}
                raw = provider.get("rawUsage") or {}
                model_id = provider.get("model") or provider.get("requestModelId") or "unknown"
                model = models.setdefault(model_id, {"model_id": model_id, "requests": 0,
                    "in": 0, "out": 0, "cache_read": 0, "reasoning": 0,
                    "total": 0, "tool_calls": 0, "duration_ms": None})
                if usage:
                    inp, out = _num(usage.get("inputTokens")), _num(usage.get("outputTokens"))
                    cached = sum(_num(x.get("cached_tokens")) for x in usage.get("inputTokensDetails") or [] if isinstance(x, dict))
                    reasoning = sum(_num(x.get("reasoning_tokens")) for x in usage.get("outputTokensDetails") or [] if isinstance(x, dict))
                    requests, total = _num(usage.get("requests")) or 1, _num(usage.get("totalTokens")) or inp + out + reasoning
                else:
                    inp, out = _num(raw.get("prompt_tokens")), _num(raw.get("completion_tokens"))
                    cached = _num(raw.get("prompt_cache_hit_tokens")) or _num(raw.get("cached_tokens"))
                    reasoning = _num(raw.get("completion_thinking_tokens"))
                    requests, total = 1, _num(raw.get("total_tokens")) or inp + out + reasoning
                model["requests"] += requests
                model["in"] += inp
                model["out"] += out
                model["cache_read"] += cached
                model["reasoning"] += reasoning
                model["total"] += total
                content = obj.get("content") or []
                model["tool_calls"] += sum(1 for item in content if isinstance(item, dict)
                                           and item.get("type") in ("tool_use", "function_call"))
        if not times or not prompts:
            continue
        start, end = min(times), max(times)
        local = datetime.fromtimestamp(start / 1000).astimezone()
        result["sessions"].append({"source": SOURCE, "variant": "codebuddy_cli",
            "backend": "codebuddy_projects_jsonl", "parent_session_id": None, "is_subagent": False,
            "session_id": sid, "day": local.strftime("%Y-%m-%d"), "start": local.strftime("%H:%M"),
            "end": datetime.fromtimestamp(end / 1000).astimezone().strftime("%H:%M"),
            "_order": local.strftime("%Y-%m-%d%H:%M"), "title": title or prompts[0][:60],
            "dir": cwd, "prompts": prompts})
    rows = sorted(models.values(), key=lambda m: -m["total"])
    for model in rows:
        model.update(in_h=fmt(model["in"]), out_h=fmt(model["out"]), total_h=fmt(model["total"]))
    result["stats_block"] = {"models": rows, "grand_total": sum(m["total"] for m in rows), "nonmain_total": 0}
    return result
