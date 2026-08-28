"""Claude Code 引擎适配器：读取 ~/.claude/projects/**/*.jsonl。

要点（spec v0.2 §5.2）：
- 仅 user/assistant 行且 isSidechain!=true 入统计；sidechain 整体剔除；
- 文件级 mtime 预筛（窗口±1天）控制历史 I/O，跳过数计入降级注记；
- usage 无 provider 级 total，按 输入+输出+推理 求和；duration_ms 恒 null。
"""
import json
import os
from datetime import datetime
from pathlib import Path

from .common import clean_text, fmt, PROMPT_CAP, MSG_CAP, TEXT_CAP

SOURCE = "claude_code"


def projects_root():
    env = os.environ.get("WORKSUMMARY_CLAUDE_DIR")
    if env:
        return Path(env)
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return Path(home) / ".claude" / "projects"


def _parse_iso_ms(ts):
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except (TypeError, ValueError, AttributeError):
        return None


def _user_texts(msg):
    out = []
    if not isinstance(msg, dict):
        return out
    c = msg.get("content")
    if isinstance(c, str):
        out.append(c)
    elif isinstance(c, list):
        for b in c:
            if isinstance(b, dict) and b.get("type") == "text":
                out.append(b.get("text") or "")
    return [clean_text(t) for t in out]


def _tool_uses(msg):
    n = 0
    c = (msg or {}).get("content")
    if isinstance(c, list):
        for b in c:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                n += 1
    return n


def _local_hhmm(ts_ms):
    d = datetime.fromtimestamp(ts_ms / 1000).astimezone()
    return d.strftime("%H:%M"), d.strftime("%Y-%m-%d"), d.strftime("%Y-%m-%d%H:%M")


def _assistant_texts(msg):
    out = []
    c = (msg or {}).get("content")
    if isinstance(c, list):
        for b in c:
            if isinstance(b, dict) and b.get("type") == "text":
                t = clean_text(b.get("text"))
                if t:
                    out.append(t)
    return out


def fetch_detail(session_id, degradations):
    """提取单个会话主线程的完整对话（跳过 sidechain）。

    返回 {found, session, messages, truncated}；未找到返回 {"found": False}。
    """
    root = projects_root()
    if not root.is_dir():
        degradations.append(f"[{SOURCE}] 目录不存在: {root}")
        return {"found": False}

    # 会话文件名通常即 sessionId，先按文件名定位，失败再全量扫描
    candidates = list(root.rglob(f"{session_id}.jsonl"))
    if not candidates:
        candidates = [p for p in sorted(root.rglob("*.jsonl"))]
    else:
        candidates = candidates[:1]

    messages, truncated = [], False
    start_ms = end_ms = None
    cwd = ""
    matched = False
    for p in candidates:
        file_hit = False
        try:
            fh = open(p, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if (o.get("sessionId") or p.stem) != session_id:
                    if file_hit:
                        continue
                    # 文件名未命中时需逐行核对 sessionId
                    if p.stem != session_id:
                        continue
                if o.get("isSidechain"):
                    continue
                typ = o.get("type")
                if typ not in ("user", "assistant"):
                    continue
                matched = file_hit = True
                ts = _parse_iso_ms(o.get("timestamp") or "")
                if ts is not None:
                    start_ms = ts if start_ms is None else min(start_ms, ts)
                    end_ms = ts if end_ms is None else max(end_ms, ts)
                cwd = o.get("cwd") or cwd
                msg = o.get("message") or {}
                texts = _user_texts(msg) if typ == "user" else _assistant_texts(msg)
                texts = [t for t in texts if len(t) >= 1]
                if not texts:
                    continue
                hhmm = ""
                if ts is not None:
                    hhmm = datetime.fromtimestamp(ts / 1000) \
                        .astimezone().strftime("%H:%M")
                for t in texts:
                    if len(messages) >= MSG_CAP:
                        truncated = True
                        break
                    messages.append({"ts": hhmm, "role": typ, "text": t[:TEXT_CAP]})
                if truncated:
                    break
        if matched or truncated:
            break
    if not matched:
        return {"found": False}
    messages.sort(key=lambda m: m["ts"])
    if start_ms is None:
        return {"found": False}
    hh_s, day, _ = _local_hhmm(start_ms)
    hh_e, _, _ = _local_hhmm(end_ms)
    title = next((m["text"][:60] for m in messages if m["role"] == "user"), "")
    meta = {
        "source": SOURCE, "variant": "claude_code", "backend": "claude_projects_jsonl",
        "session_id": session_id, "day": day, "start": hh_s, "end": hh_e,
        "title": title, "dir": cwd,
    }
    return {"found": True, "session": meta, "messages": messages,
            "truncated": truncated}


def gather(start_ts_ms, end_ts_ms, degradations):
    res = {"sessions": [], "stats_block": None, "truncated": False}
    root = projects_root()
    if not root.is_dir():
        degradations.append(f"[{SOURCE}] 目录不存在: {root}")
        return res

    pre_lo, pre_hi = start_ts_ms - 86_400_000, end_ts_ms + 86_400_000
    skipped_files = 0
    groups = {}
    agg = {}
    grand = 0

    def agg_of(model):
        return agg.setdefault(model, {
            "model_id": model, "requests": 0, "in": 0, "out": 0,
            "cache_read": 0, "reasoning": 0, "total": 0,
            "tool_calls": 0, "duration_ms": None})

    for p in sorted(root.rglob("*.jsonl")):
        try:
            mtime = int(p.stat().st_mtime * 1000)
        except OSError:
            continue
        if not (pre_lo <= mtime <= pre_hi):
            skipped_files += 1
            continue
        with open(p, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                if o.get("isSidechain"):
                    continue                      # sidechain 会话整体剔除
                typ = o.get("type")
                if typ not in ("user", "assistant"):
                    continue
                ts = _parse_iso_ms(o.get("timestamp") or "")
                if ts is None or not (start_ts_ms <= ts < end_ts_ms):
                    continue
                sid = o.get("sessionId") or p.stem
                g = groups.setdefault(sid, {
                    "prompts": [], "start": ts, "end": ts,
                    "cwd": o.get("cwd") or "", })
                g["start"] = min(g["start"], ts)
                g["end"] = max(g["end"], ts)
                g["cwd"] = o.get("cwd") or g["cwd"]
                msg = o.get("message") or {}
                if typ == "user":
                    for t in _user_texts(msg):
                        if len(t) >= 4 and len(g["prompts"]) < PROMPT_CAP \
                                and t not in g["prompts"]:
                            g["prompts"].append(t)
                else:
                    u = msg.get("usage") or {}
                    m = agg_of(msg.get("model") or "unknown")

                    def num(k):
                        try:
                            return int(u.get(k) or 0)
                        except (TypeError, ValueError):
                            return 0
                    det = u.get("output_tokens_details") or {}
                    try:
                        rs = int(det.get("reasoning_tokens") or 0)
                    except (TypeError, ValueError):
                        rs = 0
                    inp, outp = num("input_tokens"), num("output_tokens")
                    m["requests"] += 1
                    m["in"] += inp
                    m["out"] += outp
                    m["cache_read"] += num("cache_read_input_tokens")
                    m["reasoning"] += rs
                    m["tool_calls"] += _tool_uses(msg)
                    m["total"] += inp + outp + rs
                    grand += inp + outp + rs

    sessions = []
    for sid, g in groups.items():
        title = g["prompts"][0][:60] if g["prompts"] else ""
        if not title.strip():
            continue
        hh_s, day, order_s = _local_hhmm(g["start"])
        hh_e, _, _ = _local_hhmm(g["end"])
        sessions.append({
            "source": SOURCE, "variant": "claude_code", "backend": "claude_projects_jsonl",
            "parent_session_id": None, "is_subagent": False,
            "session_id": sid, "day": day,
            "start": hh_s, "end": hh_e, "_order": order_s,
            "title": title, "dir": g["cwd"], "prompts": g["prompts"],
        })

    models = sorted(agg.values(), key=lambda x: -x["total"])
    for m in models:
        m.update({"in_h": fmt(m["in"]), "out_h": fmt(m["out"]), "total_h": fmt(m["total"])})
    if skipped_files:
        degradations.append(
            f"[{SOURCE}] mtime 预筛跳过 {skipped_files} 个历史文件（窗口外）")
    res["sessions"] = sessions
    res["stats_block"] = {"models": models, "grand_total": grand, "nonmain_total": 0}
    return res
