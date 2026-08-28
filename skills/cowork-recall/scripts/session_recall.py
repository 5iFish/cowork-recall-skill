#!/usr/bin/env python3
"""cowork-recall 跨引擎会话检索入口（只读）。

在本机各 AI 编码智能体的持久化会话之上提供三种检索能力：
  search  按关键词跨来源搜索会话（标题/提问/目录命中），支持时间范围与分页
  list    按时间范围分页浏览聚合会话历史（新→旧）
  detail  查看指定会话的完整对话（user/assistant 消息脉络）

工作总结仍由 work_summary.py 承担；本脚本不输出 token 统计。
退出码：0 正常；1 参数错误；2 来源不可用或致命 schema 不兼容；3 会话未找到。
"""
import json
import sys
from datetime import date, datetime, timedelta

from adapters import ORDER, get_adapter
from adapters.zcode import FatalSchemaError
from work_summary import run_window, SOURCE_ALIAS, VALID_SOURCES

USAGE = """用法:
  session_recall.py search --query <关键词> [YYYY-MM-DD [YYYY-MM-DD]] [选项]
  session_recall.py list   [YYYY-MM-DD [YYYY-MM-DD]] [选项]
  session_recall.py detail --source <来源> --session <会话ID>

选项:
  --source auto|all|zcode|claude|codex|gemini|cline|roo|continue|opencode|qoder|workbuddy|codebuddy|kimi|trae
  --page N        页码，从 1 开始（默认 1）
  --page-size M   每页条数，1-100（默认 20）
  日期区间两端均含。search 默认最近 30 天，list 默认最近 7 天。
  detail 目前支持 zcode / claude / codex / trae 来源。"""

PAGE_SIZE_MAX = 100


def _die(msg, code):
    print(msg, file=sys.stderr)
    sys.exit(code)


def _parse_window(positional, default_days):
    """位置参数转 (start_date, end_date_不含)。无参时取最近 default_days 天。"""
    try:
        if not positional:
            end = date.today() + timedelta(days=1)
            return end - timedelta(days=default_days), end
        if len(positional) > 2:
            raise ValueError("too many args")
        start = date.fromisoformat(positional[0])
        if len(positional) == 1:
            return start, start + timedelta(days=1)
        end = date.fromisoformat(positional[1])
        if end < start:
            raise ValueError("start after end")
        return start, end + timedelta(days=1)
    except ValueError as e:
        _die(f"{e}\n{USAGE}", 1)


def _split_common(argv):
    """解析公共选项。返回 (source, page, page_size, rest)。"""
    src, page, page_size = "auto", 1, 20
    rest, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a in ("--source", "-s"):
            if i + 1 >= len(argv):
                _die(f"--source 需要取值\n{USAGE}", 1)
            src = argv[i + 1].lower()
            i += 2
        elif a.startswith("--source="):
            src = a.split("=", 1)[1].lower()
            i += 1
        elif a == "--page":
            if i + 1 >= len(argv):
                _die(f"--page 需要取值\n{USAGE}", 1)
            page = _to_int(argv[i + 1], "--page")
            i += 2
        elif a.startswith("--page="):
            page = _to_int(a.split("=", 1)[1], "--page")
            i += 1
        elif a == "--page-size":
            if i + 1 >= len(argv):
                _die(f"--page-size 需要取值\n{USAGE}", 1)
            page_size = _to_int(argv[i + 1], "--page-size")
            i += 2
        elif a.startswith("--page-size="):
            page_size = _to_int(a.split("=", 1)[1], "--page-size")
            i += 1
        else:
            rest.append(a)
            i += 1
    if src not in VALID_SOURCES:
        _die(f"未知 --source: {src}\n{USAGE}", 1)
    if page < 1:
        _die(f"--page 必须 >= 1\n{USAGE}", 1)
    if not 1 <= page_size <= PAGE_SIZE_MAX:
        _die(f"--page-size 必须在 1-{PAGE_SIZE_MAX}\n{USAGE}", 1)
    return SOURCE_ALIAS.get(src, src), page, page_size, rest


def _to_int(v, name):
    try:
        return int(v)
    except ValueError:
        _die(f"{name} 需要整数: {v}\n{USAGE}", 1)


def _paginate(items, page, page_size):
    total = len(items)
    off = (page - 1) * page_size
    return items[off:off + page_size], {
        "total": total, "page": page, "page_size": page_size,
        "has_more": off + page_size < total,
    }


def _gather_sessions(start, end, source):
    """调用 work_summary.run_window 并校验致命失败。返回 result dict。"""
    result = run_window(start, end, source)
    if not result["sources"] and source != "auto":
        _die(f"[{source}] 所选来源在本机不可用\n提示: 未找到对应智能体的本地数据目录/库", 2)
    if result.pop("_all_engines_fatal", False):
        _die("全部所选来源均无法读取（schema 不兼容或数据库损坏）。"
             "\n请确认各智能体客户端版本后重试。", 2)
    result.pop("_all_engines_fatal", None)
    return result


def _match_session(session, terms):
    """返回命中的内容片段列表；未命中返回 None。"""
    hits = []
    title = session.get("title") or ""
    if any(t in title.lower() for t in terms):
        hits.append("title: " + title)
    for p in session.get("prompts") or []:
        if any(t in p.lower() for t in terms):
            hits.append(p)
    if any(t in (session.get("dir") or "").lower() for t in terms):
        hits.append("dir: " + session["dir"])
    return hits or None


def cmd_search(argv):
    query = None
    rest, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a in ("--query", "-q"):
            if i + 1 >= len(argv):
                _die(f"search 需要 --query <关键词>\n{USAGE}", 1)
            query = argv[i + 1]
            i += 2
        elif a.startswith("--query="):
            query = a.split("=", 1)[1]
            i += 1
        else:
            rest.append(a)
            i += 1
    if not query or not query.strip():
        _die(f"search 需要 --query <关键词>\n{USAGE}", 1)
    source, page, page_size, positional = _split_common(rest)
    start, end = _parse_window(positional, default_days=30)

    result = _gather_sessions(start, end, source)
    terms = [t.lower() for t in query.split() if t.strip()] or [query.lower()]
    matched = []
    for s in result["sessions"]:
        hits = _match_session(s, terms)
        if hits:
            item = dict(s)
            item["matches"] = hits
            matched.append(item)
    matched.sort(key=lambda x: (x["day"], x["start"]), reverse=True)
    page_items, page_info = _paginate(matched, page, page_size)
    print(json.dumps({
        "mode": "search", "query": query,
        "window": result["window"], "sources": result["sources"],
        **page_info, "sessions": page_items,
        "truncated": result["truncated"],
        "degradations": result["degradations"], "meta": result["meta"],
    }, ensure_ascii=False))


def cmd_list(argv):
    source, page, page_size, positional = _split_common(argv)
    start, end = _parse_window(positional, default_days=7)
    result = _gather_sessions(start, end, source)
    ordered = sorted(result["sessions"],
                     key=lambda x: (x["day"], x["start"]), reverse=True)
    page_items, page_info = _paginate(ordered, page, page_size)
    print(json.dumps({
        "mode": "list",
        "window": result["window"], "sources": result["sources"],
        **page_info, "sessions": page_items,
        "truncated": result["truncated"],
        "degradations": result["degradations"], "meta": result["meta"],
    }, ensure_ascii=False))


def cmd_detail(argv):
    source = session_id = None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--session":
            if i + 1 >= len(argv):
                _die(f"detail 需要 --session <会话ID>\n{USAGE}", 1)
            session_id = argv[i + 1]
            i += 2
        elif a.startswith("--session="):
            session_id = a.split("=", 1)[1]
            i += 1
        elif a in ("--source", "-s"):
            if i + 1 >= len(argv):
                _die(f"detail 需要 --source <来源>\n{USAGE}", 1)
            source = argv[i + 1].lower()
            i += 2
        elif a.startswith("--source="):
            source = a.split("=", 1)[1].lower()
            i += 1
        else:
            _die(f"detail 未知参数: {a}\n{USAGE}", 1)
    if not source or not session_id:
        _die(f"detail 需要 --source 与 --session\n{USAGE}", 1)
    if source not in VALID_SOURCES:
        _die(f"未知 --source: {source}\n{USAGE}", 1)
    source = SOURCE_ALIAS.get(source, source)
    if source == "auto" or source not in ORDER:
        _die(f"detail 需要显式来源（zcode/claude/codex 等），收到: {source}\n{USAGE}", 1)

    try:
        adapter = get_adapter(source)
    except KeyError:
        _die(f"未知来源: {source}\n{USAGE}", 1)
    fetch = getattr(adapter, "fetch_detail", None)
    if fetch is None:
        _die(f"[{source}] 该来源暂未实现会话详情提取；"
             f"可先用 search/list 查看其标题与提问摘要", 2)
    degradations = []
    try:
        detail = fetch(session_id, degradations)
    except FatalSchemaError as ex:
        _die(f"[{source}] schema 不兼容：{ex}", 2)
    except OSError as ex:
        _die(f"[{source}] 读取失败: {ex}", 2)
    if not detail.get("found"):
        print(json.dumps({
            "mode": "detail", "source": source, "session_id": session_id,
            "found": False,
            "hint": "会话未找到。请确认 --source 与 search/list 输出中的 "
                    "source、session_id 一致",
            "degradations": list(dict.fromkeys(degradations)),
        }, ensure_ascii=False))
        sys.exit(3)
    off = datetime.now().astimezone().utcoffset() or timedelta(0)
    sign = "+" if off.total_seconds() >= 0 else "-"
    mins = abs(off).seconds // 60
    print(json.dumps({
        "mode": "detail", "source": source, "session_id": session_id,
        **detail,
        "message_count": len(detail.get("messages") or []),
        "degradations": list(dict.fromkeys(degradations)),
        "meta": {"timezone": f"UTC{sign}{mins // 60:02d}:{mins % 60:02d}"},
    }, ensure_ascii=False))


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        sys.exit(0 if argv else 1)
    cmd = argv[0]
    if cmd == "search":
        cmd_search(argv[1:])
    elif cmd == "list":
        cmd_list(argv[1:])
    elif cmd == "detail":
        cmd_detail(argv[1:])
    else:
        _die(f"未知子命令: {cmd}\n{USAGE}", 1)


if __name__ == "__main__":
    main()
