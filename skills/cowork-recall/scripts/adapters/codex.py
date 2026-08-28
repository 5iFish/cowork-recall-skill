"""Codex 引擎适配器：读取 ~/.codex/sessions/YYYY/MM/DD/*.jsonl（rollout）。

要点（spec v0.2 §5.3）：
- 目录结构自带日期索引，仅枚举窗口内日期子目录；
- usage 取 event_msg/token_count 的 info.total_token_usage，模型名优先 info.model，
  其次最近一次 turn_context.payload.model，均缺归 "unknown" 并记一次性注记；
- 无子代理概念，nonmain_total 恒 0。
"""
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

from .common import clean_text, fmt, PROMPT_CAP, MSG_CAP, TEXT_CAP

SOURCE = "codex"

# rollout 会把注入上下文当作 user 消息写入，需按前缀识别并跳过
NOISE_USER = re.compile(
    r"^(#\s*AGENTS\.md\b|<user_instructions\b|<environment_context\b"
    r"|<permissions\b|<cwd\b|<env\b|#\s*Options\b|#\s*claude tips\b)", re.I)


def is_noise(t):
    return bool(NOISE_USER.match(t))


def sessions_root():
    env = os.environ.get("WORKSUMMARY_CODEX_DIR")
    if env:
        return Path(env)
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return Path(home) / ".codex" / "sessions"


def _parse_iso_ms(ts):
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except (TypeError, ValueError, AttributeError):
        return None


def _window_dates(start_ms, end_ms):
    d0 = datetime.fromtimestamp(start_ms / 1000).astimezone().date()
    d1 = datetime.fromtimestamp((end_ms - 1) / 1000).astimezone().date()
    out, d = [], d0
    while d <= d1:
        out.append(d)
        d += timedelta(days=1)
    return out


def _texts_from_content(content):
    out = []
    if not isinstance(content, list):
        return out
    for b in content:
        if isinstance(b, dict):
            t = b.get("text") or b.get("input_text") or ""
            t = clean_text(t)
            if len(t) >= 4:
                out.append(t)
    return out


def fetch_detail(session_id, degradations):
    """提取单个 rollout 会话的完整对话（user/assistant 文本，按时间升序）。

    返回 {found, session, messages, truncated}；未找到返回 {"found": False}。
    """
    root = sessions_root()
    if not root.is_dir():
        degradations.append(f"[{SOURCE}] 目录不存在: {root}")
        return {"found": False}

    # rollout 文件名包含 session_id，先按文件名过滤再解析 session_meta 确认
    candidates = [p for p in sorted(root.rglob("*.jsonl"))
                  if session_id in p.name]
    if not candidates:
        candidates = sorted(root.rglob("*.jsonl"))

    for p in candidates:
        messages, truncated = [], False
        meta = None
        start_ms = end_ms = None
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
                ts = _parse_iso_ms(o.get("timestamp") or "")
                typ = o.get("type")
                payload = o.get("payload") or {}
                if typ == "session_meta":
                    sid = payload.get("session_id") or payload.get("id") or p.stem
                    if sid != session_id:
                        meta = None
                        break                    # 不是目标会话，换下一个文件
                    meta = {
                        "session_id": sid,
                        "cwd": payload.get("cwd") or "",
                        "ts": _parse_iso_ms(payload.get("timestamp")) or ts,
                    }
                    continue
                if meta is None:
                    continue
                if typ != "response_item" or payload.get("type") != "message":
                    continue
                role = payload.get("role")
                if role not in ("user", "assistant"):
                    continue
                texts = _texts_from_content(payload.get("content"))
                if role == "user":
                    texts = [t for t in texts if not is_noise(t)]
                if not texts:
                    continue
                if ts is not None:
                    start_ms = ts if start_ms is None else min(start_ms, ts)
                    end_ms = ts if end_ms is None else max(end_ms, ts)
                hhmm = ""
                if ts is not None:
                    hhmm = datetime.fromtimestamp(ts / 1000) \
                        .astimezone().strftime("%H:%M")
                for t in texts:
                    if len(messages) >= MSG_CAP:
                        truncated = True
                        break
                    messages.append({"ts": hhmm, "role": role, "text": t[:TEXT_CAP]})
                if truncated:
                    break
        if meta is None:
            continue
        base_ms = meta["ts"] if meta["ts"] is not None else start_ms
        if base_ms is None:
            return {"found": False}
        d0 = datetime.fromtimestamp(base_ms / 1000).astimezone()
        hh_e = datetime.fromtimestamp(
            (end_ms or base_ms) / 1000).astimezone().strftime("%H:%M")
        title = next((m["text"][:60] for m in messages if m["role"] == "user"), "")
        sess = {
            "source": SOURCE, "variant": "codex", "backend": "codex_rollout_jsonl",
            "session_id": session_id,
            "day": d0.strftime("%Y-%m-%d"), "start": d0.strftime("%H:%M"),
            "end": hh_e, "title": title, "dir": meta["cwd"],
        }
        return {"found": True, "session": sess, "messages": messages,
                "truncated": truncated}
    return {"found": False}


def gather(start_ts_ms, end_ts_ms, degradations):
    res = {"sessions": [], "stats_block": None, "truncated": False}
    root = sessions_root()
    if not root.is_dir():
        degradations.append(f"[{SOURCE}] 目录不存在: {root}")
        return res

    agg = {}
    grand = 0
    unknown_seen = False
    sessions = []

    def agg_of(model):
        return agg.setdefault(model, {
            "model_id": model, "requests": 0, "in": 0, "out": 0,
            "cache_read": 0, "reasoning": 0, "total": 0,
            "tool_calls": None, "duration_ms": None})

    n_req_total = 0   # 每条 token_count 视作一轮请求累计到对应模型
    for day in _window_dates(start_ts_ms, end_ts_ms):
        pdir = root / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
        if not pdir.is_dir():
            continue
        for p in sorted(pdir.glob("*.jsonl")):
            meta = None            # (sid, cwd, ts_start)
            g = {"prompts": [], "cwd": "", "start": None, "end": None}
            turn_model = None
            with open(p, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    ts = _parse_iso_ms(o.get("timestamp") or "")
                    typ = o.get("type")
                    payload = o.get("payload") or {}
                    if typ == "session_meta":
                        sid = payload.get("session_id") or payload.get("id") or p.stem
                        meta_ts = _parse_iso_ms(payload.get("timestamp")) or ts
                        meta = {
                            "session_id": sid,
                            "cwd": payload.get("cwd") or "",
                            "ts": meta_ts,
                        }
                        if meta_ts is not None and start_ts_ms <= meta_ts < end_ts_ms:
                            g["start"] = meta_ts
                            g["cwd"] = meta["cwd"]
                        continue
                    if ts is None or not (start_ts_ms <= ts < end_ts_ms):
                        continue
                    if g["start"] is None:
                        # 该 rollout 起始时间不在窗口内（跨日续写等）→ 整文件跳过
                        continue
                    if typ == "response_item" and payload.get("type") == "message" \
                            and payload.get("role") == "user":
                        for t in _texts_from_content(payload.get("content")):
                            if is_noise(t):
                                continue
                            if len(g["prompts"]) < PROMPT_CAP and t not in g["prompts"]:
                                g["prompts"].append(t)
                    elif typ == "turn_context":
                        turn_model = payload.get("model") or turn_model
                        g["cwd"] = payload.get("cwd") or g["cwd"]
                    elif typ == "event_msg" and payload.get("type") == "token_count":
                        info = payload.get("info") or {}
                        tu = info.get("total_token_usage") or {}
                        model = info.get("model") or turn_model
                        if not model:
                            model = "unknown"
                            unknown_seen = True
                        m = agg_of(model)

                        def num(k, src=tu):
                            try:
                                return int(src.get(k) or 0)
                            except (TypeError, ValueError):
                                return 0
                        inp, outp = num("input_tokens"), num("output_tokens")
                        rs = num("reasoning_output_tokens")
                        m["requests"] += 1
                        n_req_total += 1
                        m["in"] += inp
                        m["out"] += outp
                        m["cache_read"] += num("cached_input_tokens")
                        m["reasoning"] += rs
                        m["total"] += inp + outp + rs
                        grand += inp + outp + rs
                    g["end"] = max(g["end"] or ts, ts)
            if meta and g["start"] is not None:
                title = g["prompts"][0][:60] if g["prompts"] else ""
                hh_s, day_str, order_s = (lambda d: (
                    d.strftime("%H:%M"), d.strftime("%Y-%m-%d"),
                    d.strftime("%Y-%m-%d%H:%M")))(
                    datetime.fromtimestamp(g["start"] / 1000).astimezone())
                hh_e = datetime.fromtimestamp(
                    (g["end"] or g["start"]) / 1000).astimezone().strftime("%H:%M")
                sessions.append({
                    "source": SOURCE, "variant": "codex", "backend": "codex_rollout_jsonl",
                    "parent_session_id": None, "is_subagent": False,
                    "session_id": meta["session_id"],
                    "day": day_str, "start": hh_s, "end": hh_e,
                    "_order": order_s, "title": title,
                    "dir": g["cwd"], "prompts": g["prompts"],
                })

    models = sorted(agg.values(), key=lambda x: -x["total"])
    for m in models:
        m.update({"in_h": fmt(m["in"]), "out_h": fmt(m["out"]), "total_h": fmt(m["total"]),
                  "tool_calls": m["tool_calls"], "duration_ms": None})
    if unknown_seen:
        degradations.append(f"[{SOURCE}] 部分 token 统计缺少模型名，已归入 unknown")
    res["sessions"] = sessions
    res["stats_block"] = {"models": models, "grand_total": grand, "nonmain_total": 0}
    return res
