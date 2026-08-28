"""OpenCode 当前 SQLite 与 legacy JSON 适配器。"""
import json
import os
from datetime import datetime
from pathlib import Path

from .common import clean_text, columns, fmt, open_ro, table_names

SOURCE = "opencode"


def _paths():
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    root = Path(os.environ.get("WORKSUMMARY_OPENCODE_DIR") or home / ".local" / "share" / "opencode")
    db = Path(os.environ.get("WORKSUMMARY_OPENCODE_DB") or root / "opencode.db")
    return root, db


def _num(value):
    try: return int(value or 0)
    except (TypeError, ValueError): return 0


def gather(start_ts_ms, end_ts_ms, degradations):
    result = {"sessions": [], "stats_block": None, "truncated": False}
    root, db = _paths()
    sessions, models = {}, {}
    if db.is_file():
        con = open_ro(str(db))
        try:
            tables = table_names(con.cursor())
            cols = columns(con.cursor(), "session")
            if "session" not in tables or not {"id", "time_created"} <= cols:
                degradations.append(f"[{SOURCE}] session schema 不兼容")
            else:
                optional = [x for x in ("directory", "title", "time_updated", "tokens_input", "tokens_output",
                    "tokens_reasoning", "tokens_cache_read", "model") if x in cols]
                selected = ["id", "time_created", *optional]
                sql = f"SELECT {','.join(selected)} FROM session WHERE CAST(time_created AS INTEGER)>=? AND CAST(time_created AS INTEGER)<?"
                for row in con.execute(sql, (start_ts_ms, end_ts_ms)):
                    data = dict(zip(selected, row)); sid = str(data["id"])
                    prompts = []
                    if {"message", "part"} <= tables:
                        user_ids = set()
                        for mid, raw in con.execute("SELECT id,data FROM message WHERE session_id=?", (sid,)):
                            try: obj = json.loads(raw)
                            except (TypeError, json.JSONDecodeError): continue
                            if obj.get("role") == "user": user_ids.add(mid)
                        for mid, raw in con.execute("SELECT message_id,data FROM part WHERE session_id=? ORDER BY time_created", (sid,)):
                            if mid not in user_ids: continue
                            try: obj = json.loads(raw)
                            except (TypeError, json.JSONDecodeError): continue
                            text = clean_text(obj.get("text") or "") if obj.get("type") == "text" else ""
                            if len(text) >= 4 and text not in prompts: prompts.append(text)
                    sessions[sid] = {"start": _num(data["time_created"]), "end": _num(data.get("time_updated")) or _num(data["time_created"]),
                        "cwd": data.get("directory") or "", "title": data.get("title") or "", "prompts": prompts,
                        "variant": "opencode_cli", "backend": "opencode_sqlite"}
                    inp, out, reasoning = _num(data.get("tokens_input")), _num(data.get("tokens_output")), _num(data.get("tokens_reasoning"))
                    if inp or out or reasoning or _num(data.get("tokens_cache_read")):
                        model_id = data.get("model") or "unknown"
                        model = models.setdefault(model_id, {"model_id": model_id, "requests": 0,
                            "in": 0, "out": 0, "cache_read": 0, "reasoning": 0, "total": 0,
                            "tool_calls": None, "duration_ms": None})
                        model["requests"] += 1; model["in"] += inp; model["out"] += out
                        model["cache_read"] += _num(data.get("tokens_cache_read")); model["reasoning"] += reasoning
                        model["total"] += inp + out + reasoning
        finally: con.close()
    legacy_root = root / "session"
    if legacy_root.is_dir():
        for path in legacy_root.rglob("*.json"):
            try: obj = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError): continue
            sid = str(obj.get("id") or path.stem)
            if sid in sessions: continue
            times = obj.get("time") or {}
            created = _num(times.get("created") or obj.get("time_created"))
            if not start_ts_ms <= created < end_ts_ms: continue
            sessions[sid] = {"start": created, "end": _num(times.get("updated")) or created,
                "cwd": obj.get("directory") or "", "title": obj.get("title") or "", "prompts": [],
                "variant": "opencode_legacy", "backend": "opencode_legacy_json"}
    if not db.is_file() and not legacy_root.is_dir():
        degradations.append(f"[{SOURCE}] 当前数据库和 legacy 历史均不存在")
    for sid, data in sessions.items():
        local = datetime.fromtimestamp(data["start"] / 1000).astimezone()
        result["sessions"].append({"source": SOURCE, "variant": data["variant"], "backend": data["backend"],
            "parent_session_id": None, "is_subagent": False, "session_id": sid,
            "day": local.strftime("%Y-%m-%d"), "start": local.strftime("%H:%M"),
            "end": datetime.fromtimestamp(data["end"] / 1000).astimezone().strftime("%H:%M"),
            "_order": local.strftime("%Y-%m-%d%H:%M"), "title": data["title"] or (data["prompts"][0][:60] if data["prompts"] else ""),
            "dir": data["cwd"], "prompts": data["prompts"][:6]})
    rows = sorted(models.values(), key=lambda m: -m["total"])
    for model in rows: model.update(in_h=fmt(model["in"]), out_h=fmt(model["out"]), total_h=fmt(model["total"]))
    result["stats_block"] = {"models": rows, "grand_total": sum(m["total"] for m in rows), "nonmain_total": 0}
    return result
