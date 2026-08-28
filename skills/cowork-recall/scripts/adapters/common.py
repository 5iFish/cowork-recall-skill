"""跨引擎共享工具：清洗、格式化、SQLite 只读访问。"""
import re
import sqlite3

SESSION_CAP = 120
PROMPT_CAP = 6
MSG_CAP = 200        # 会话详情消息数上限
TEXT_CAP = 2000      # 会话详情单条消息字符上限

XML_PAIR = re.compile(
    r"<(system-reminder|file|task-notification|agentsMd|context|environment)>"
    r".*?</\1>", re.S | re.I)
XML_ORPHAN = re.compile(
    r"<(/?)(system-reminder|file|task-notification|agentsMd|context|environment)"
    r"[^>]*>", re.I)
CAVEAT_LINE = re.compile(r"^\s*Caveat:.*$", re.I | re.M)


def clean_text(t):
    t = XML_PAIR.sub(" ", t or "")
    t = XML_ORPHAN.sub(" ", t)
    t = CAVEAT_LINE.sub(" ", t)
    return " ".join(t.split()).strip()


def fmt(n):
    n = int(n or 0)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def columns(cur, table):
    try:
        return {r[1] for r in cur.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def table_names(cur):
    return {t[0] for t in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def open_ro(path):
    """只读打开，偶发 busy 重试 1 次；失败抛最后一个 sqlite3.Error。"""
    last = None
    for _ in range(2):
        try:
            return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        except sqlite3.Error as e:
            last = e
    raise last
