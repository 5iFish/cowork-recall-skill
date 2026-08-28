"""ZCode 引擎适配器：读取 ~/.zcode/cli/db/db.sqlite（v0.1 逻辑平移）。"""
import json
import os
import sqlite3
from datetime import datetime

from .common import (clean_text, columns, fmt, open_ro, table_names,
                     PROMPT_CAP, SESSION_CAP, MSG_CAP, TEXT_CAP)

CORE_REQUIRED = {"id", "title", "directory", "time_created"}
USAGE_REQUIRED = {"started_at", "model_id", "input_tokens", "output_tokens"}
SOURCE = "zcode"


class FatalSchemaError(Exception):
    """session 表缺失 L0 必需列，无法继续。"""


def db_path():
    for k in ("WORKSUMMARY_ZCODE_DB", "ZCODE_DB_PATH"):
        if os.environ.get(k):
            return os.environ[k]
    home = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(home, ".zcode", "cli", "db", "db.sqlite")


def _heuristic_session_where(scols):
    """task_type/parent_id 缺失时的降级过滤。返回 (where附加条件, 降级注记|None)。"""
    if {"task_type", "parent_id"} <= scols:
        return " AND task_type='interactive' AND parent_id IS NULL", None
    note = "[zcode] session 缺少 task_type/parent_id，启用标题启发式过滤"
    where = (" AND title NOT LIKE '你是%' AND title NOT LIKE 'Fork of %'"
             " AND title != '' AND title IS NOT NULL")
    return where, note


def fetch_prompts(cur, sid, tables, degradations):
    out = []
    ih_ok = "input_history" in tables and \
        {"session_id", "text", "time_created"} <= columns(cur, "input_history")
    if ih_ok:
        for (t,) in cur.execute(
                "SELECT text FROM input_history WHERE session_id=? "
                "ORDER BY CAST(time_created AS INTEGER)", (sid,)):
            ct = clean_text(t)
            if len(ct) >= 4 and ct not in out:
                out.append(ct)
            if len(out) >= PROMPT_CAP:
                return out
    if not out and {"message", "part"} <= tables:
        try:
            umids = {r[0] for r in cur.execute(
                "SELECT id FROM message WHERE session_id=? "
                "AND CAST(json_extract(data,'$.role') AS TEXT)='user'", (sid,))}
            for pmid, pd in cur.execute(
                    "SELECT message_id, data FROM part WHERE session_id=? "
                    "ORDER BY time_created", (sid,)):
                if pmid not in umids:
                    continue
                try:
                    obj = json.loads(pd)
                except Exception:
                    continue
                if obj.get("type") != "text":
                    continue
                ct = clean_text(obj.get("text"))
                if len(ct) >= 4 and ct not in out:
                    out.append(ct)
                if len(out) >= PROMPT_CAP:
                    break
            if out:
                degradations.append(f"[{SOURCE}] 部分会话无 input_history 记录，改用 message/part 提取提问")
        except sqlite3.Error:
            degradations.append(f"[{SOURCE}] message/part 提取失败，仅用标题")
    if not out and "input_history" not in tables:
        degradations.append(f"[{SOURCE}] input_history 表不可用，仅用标题")
    return out


def extract_sessions(con, start_ts_ms, end_ts_ms, degradations):
    cur = con.cursor()
    scols = columns(cur, "session")
    missing = CORE_REQUIRED - scols
    if missing:
        raise FatalSchemaError(f"session 缺少核心列: {sorted(missing)}")
    extra, heuristic_note = _heuristic_session_where(scols)
    if heuristic_note:
        degradations.append(heuristic_note)
    sql = (f"SELECT id,title,directory,time_created,time_updated FROM session "
           f"WHERE CAST(time_created AS INTEGER)>=? AND CAST(time_created AS INTEGER)<?"
           f"{extra} ORDER BY CAST(time_created AS INTEGER) DESC LIMIT ?")
    rows = cur.execute(sql, (start_ts_ms, end_ts_ms, SESSION_CAP + 1)).fetchall()
    truncated = len(rows) > SESSION_CAP
    rows = rows[:SESSION_CAP]
    tables = table_names(cur)
    sessions = []
    for sid, title, directory, tc, tu in rows:
        title = title or ""
        local_c = datetime.fromtimestamp(int(tc) / 1000).astimezone()
        prompts = fetch_prompts(cur, sid, tables, degradations)
        if not title.strip() and not prompts:
            continue
        sessions.append({
            "source": SOURCE,
            "variant": "zcode",
            "backend": "zcode_sqlite",
            "parent_session_id": None,
            "is_subagent": False,
            "session_id": sid,
            "day": local_c.strftime("%Y-%m-%d"),
            "start": local_c.strftime("%H:%M"),
            "end": datetime.fromtimestamp(int(tu) / 1000).astimezone().strftime("%H:%M"),
            "_order": local_c.strftime("%Y-%m-%d%H:%M"),
            "title": title.strip(),
            "dir": directory or "",
            "prompts": prompts,
        })
    return sessions, truncated


def collect_stats(con, start_ts_ms, end_ts_ms, degradations):
    cur = con.cursor()
    tables = table_names(cur)
    if "model_usage" not in tables:
        degradations.append(f"[{SOURCE}] model_usage 表不存在，token 统计不可用")
        return None
    ucols = columns(cur, "model_usage")
    if not USAGE_REQUIRED <= ucols:
        degradations.append(f"[{SOURCE}] model_usage 缺少必需列: {sorted(USAGE_REQUIRED - ucols)}")
        return None
    opt = ["cache_read_input_tokens", "reasoning_tokens", "tool_call_count",
           "duration_ms", "query_source"]
    sel = ["started_at", "model_id", "input_tokens", "output_tokens"] + \
          [c for c in opt if c in ucols]
    has_comp = "computed_total_tokens" in ucols
    if has_comp:
        sel.append("computed_total_tokens")
    else:
        degradations.append(f"[{SOURCE}] 缺少 computed_total_tokens，total 改用 输入+输出+推理 求和")
    sql = (f"SELECT {','.join(sel)} FROM model_usage "
           f"WHERE CAST(started_at AS INTEGER)>=? AND CAST(started_at AS INTEGER)<=?")
    agg = {}
    grand = sub = 0

    def num(d, k):
        try:
            return int(d.get(k) or 0)
        except (TypeError, ValueError):
            return 0

    for row in cur.execute(sql, (start_ts_ms, end_ts_ms - 1)):
        d = dict(zip(sel, row))
        comp = num(d, "computed_total_tokens") if has_comp else 0
        total = comp or (num(d, "input_tokens") + num(d, "output_tokens")
                         + num(d, "reasoning_tokens"))
        m = agg.setdefault(d["model_id"], {
            "model_id": d["model_id"], "requests": 0, "in": 0, "out": 0,
            "cache_read": 0, "reasoning": 0, "total": 0,
            "tool_calls": 0, "duration_ms": 0})
        m["requests"] += 1
        m["in"] += num(d, "input_tokens")
        m["out"] += num(d, "output_tokens")
        m["cache_read"] += num(d, "cache_read_input_tokens")
        m["reasoning"] += num(d, "reasoning_tokens")
        m["tool_calls"] += num(d, "tool_call_count")
        m["duration_ms"] += num(d, "duration_ms")
        m["total"] += total
        grand += total
        if (d.get("query_source") or "main_turn") != "main_turn":
            sub += total
    models = sorted(agg.values(), key=lambda x: -x["total"])
    for m in models:
        m.update({"in_h": fmt(m["in"]), "out_h": fmt(m["out"]), "total_h": fmt(m["total"])})
    return {"models": models, "grand_total": grand, "nonmain_total": sub}


def gather(start_ts_ms, end_ts_ms, degradations):
    """统一接口。返回 {sessions, stats_block, truncated}；库级致命错误上抛。"""
    out = {"sessions": [], "stats_block": None, "truncated": False}
    con = open_ro(db_path())
    s_degs = []
    try:
        sessions, truncated = extract_sessions(con, start_ts_ms, end_ts_ms, s_degs)
        stats_block = collect_stats(con, start_ts_ms, end_ts_ms, s_degs)
    finally:
        con.close()
    out.update(sessions=sessions, stats_block=stats_block, truncated=truncated)
    for d in s_degs:
        item = d if d.startswith("[zcode]") else f"[{SOURCE}] {d}"
        degradations.append(item)
    return out


def fetch_detail(session_id, degradations):
    """提取单个会话的完整对话（user/assistant 文本消息，按时间升序）。

    返回 {found, session, messages, truncated}；未找到返回 {"found": False}。
    """
    con = open_ro(db_path())
    try:
        cur = con.cursor()
        scols = columns(cur, "session")
        missing = CORE_REQUIRED - scols
        if missing:
            raise FatalSchemaError(f"session 缺少核心列: {sorted(missing)}")
        row = cur.execute(
            "SELECT id,title,directory,time_created,time_updated FROM session "
            "WHERE id=?", (session_id,)).fetchone()
        if not row:
            return {"found": False}
        sid, title, directory, tc, tu = row
        local_c = datetime.fromtimestamp(int(tc) / 1000).astimezone()
        meta = {
            "source": SOURCE, "variant": "zcode", "backend": "zcode_sqlite",
            "session_id": sid,
            "day": local_c.strftime("%Y-%m-%d"),
            "start": local_c.strftime("%H:%M"),
            "end": datetime.fromtimestamp(int(tu) / 1000).astimezone().strftime("%H:%M"),
            "title": (title or "").strip(),
            "dir": directory or "",
        }
        tables = table_names(cur)
        if not {"message", "part"} <= tables:
            degradations.append(f"[{SOURCE}] message/part 表不可用，仅返回会话元信息")
            return {"found": True, "session": meta, "messages": [], "truncated": False}
        roles = {}
        for mid, data in cur.execute(
                "SELECT id, data FROM message WHERE session_id=?", (sid,)):
            try:
                r = json.loads(data).get("role")
            except Exception:
                continue
            if r in ("user", "assistant"):
                roles[mid] = r
        messages, truncated = [], False
        for pmid, ptc, pd in cur.execute(
                "SELECT message_id, time_created, data FROM part "
                "WHERE session_id=? ORDER BY time_created", (sid,)):
            role = roles.get(pmid)
            if role is None:
                continue
            try:
                obj = json.loads(pd)
            except Exception:
                continue
            if obj.get("type") != "text":
                continue
            ct = clean_text(obj.get("text"))
            if len(ct) < 1:
                continue
            try:
                hhmm = datetime.fromtimestamp(
                    int(ptc) / 1000).astimezone().strftime("%H:%M")
            except (TypeError, ValueError, OSError):
                hhmm = ""
            if len(messages) >= MSG_CAP:
                truncated = True
                break
            messages.append({"ts": hhmm, "role": role, "text": ct[:TEXT_CAP]})
        return {"found": True, "session": meta, "messages": messages,
                "truncated": truncated}
    finally:
        con.close()
