#!/usr/bin/env python3
"""cowork-recall 跨引擎数据提取入口（只读）。

从已检测到的智能体本地数据（ZCode / Claude Code / Codex）提取指定窗口的
会话脉络与按模型 token 统计，合并输出 JSON 到 stdout。
退出码：0 正常；1 参数错误；2 全部所选来源不可用。
"""
import json
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta

from adapters import ORDER, get_adapter, detect_sources
from adapters.common import fmt                      # 兼容旧测试的再导出
from markdown_renderer import render_markdown
from adapters.zcode import (FatalSchemaError,        # 兼容再导出（行为契约不变）
                            SESSION_CAP, PROMPT_CAP,
                            extract_sessions, collect_stats,
                            clean_text, db_path)

USAGE = ("用法: work_summary.py [--source auto|all|zcode|claude|codex|gemini|cline|roo|continue|"
         "opencode|qoder|workbuddy|codebuddy|kimi|trae] [--format json|markdown] "
         "[YYYY-MM-DD [YYYY-MM-DD]]  （区间两端均含，默认今天）")
VALID_SOURCES = {"auto", "all", *ORDER, "claude", "roo", "kimi"}
SOURCE_ALIAS = {"claude": "claude_code", "roo": "roo_code", "kimi": "kimi_code", "all": "auto"}


def _die(msg, code):
    print(msg, file=sys.stderr)
    sys.exit(code)


def _split_argv(argv):
    pos, i = [], 0
    src, output_format = "auto", "json"
    while i < len(argv):
        a = argv[i]
        if a in ("--source", "-s"):
            if i + 1 >= len(argv):
                _die(f"--source 需要取值\n{USAGE}", 1)
            src = argv[i + 1].lower()
            i += 2
            continue
        if a.startswith("--source="):
            src = a.split("=", 1)[1].lower()
            i += 1
            continue
        if a in ("--format", "-f"):
            if i + 1 >= len(argv):
                _die(f"--format 需要取值\n{USAGE}", 1)
            output_format = argv[i + 1].lower()
            i += 2
            continue
        if a.startswith("--format="):
            output_format = a.split("=", 1)[1].lower()
            i += 1
            continue
        pos.append(a)
        i += 1
    if src not in VALID_SOURCES:
        _die(f"未知 --source: {src}\n{USAGE}", 1)
    if output_format not in {"json", "markdown"}:
        _die(f"未知 --format: {output_format}\n{USAGE}", 1)
    return src, SOURCE_ALIAS.get(src, src), output_format, pos


def parse_args(argv):
    try:
        if not argv:
            today = date.today()
            return today, today + timedelta(days=1)
        if len(argv) > 2:
            raise ValueError("too many args")
        start = date.fromisoformat(argv[0])
        if len(argv) == 1:
            return start, start + timedelta(days=1)
        end = date.fromisoformat(argv[1])
        if end <= start:
            raise ValueError("start after end")
        return start, end + timedelta(days=1)
    except ValueError as e:
        _die(f"{e}\n{USAGE}", 1)


def run_window(start, end, source):
    """返回 result dict；合并规则见 spec v0.2 §4.4。"""
    s_dt = datetime(start.year, start.month, start.day).astimezone()
    e_dt = datetime(end.year, end.month, end.day).astimezone()
    s_ms, e_ms = int(s_dt.timestamp() * 1000), int(e_dt.timestamp() * 1000)

    enabled = [s for s in detect_sources() if source == "auto" or s == source]
    degradations = []
    sessions, stats_by_source = [], {}
    grand = sub = 0
    truncated = False
    hard_failures = set()

    if not enabled and source != "auto":
        degradations.append(f"[{source}] 本机未检测到该来源的本地数据")

    for name in enabled:
        try:
            adapter = get_adapter(name)
        except KeyError:
            continue
        try:
            body = adapter.gather(s_ms, e_ms, degradations)
        except FatalSchemaError as ex:
            hard_failures.add(name)
            degradations.append(f"[{name}] schema 不兼容：{ex}")
            continue
        except (sqlite3.Error, OSError) as ex:
            hard_failures.add(name)
            degradations.append(f"[{name}] 提取失败: {ex}")
            continue
        sessions.extend(body["sessions"])
        truncated = truncated or body["truncated"]
        blk = body["stats_block"]
        if blk is None:
            continue
        stats_by_source[name] = blk
        grand += blk.get("grand_total") or 0
        sub += blk.get("nonmain_total") or 0

    sessions.sort(key=lambda x: x["_order"])
    for s in sessions:
        s.pop("_order", None)

    off = datetime.now().astimezone().utcoffset() or timedelta(0)
    sign = "+" if off.total_seconds() >= 0 else "-"
    mins = abs(off).seconds // 60
    all_fatal = bool(enabled) and set(enabled) <= hard_failures
    return {
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "sources": enabled,
        "sessions": sessions,
        "stats": {"by_source": stats_by_source, "grand_total": grand, "nonmain_total": sub},
        "degradations": list(dict.fromkeys(degradations)),
        "skipped_git_dirs": [],
        "truncated": truncated,
        "_all_engines_fatal": all_fatal,
        "meta": {"timezone": f"UTC{sign}{mins // 60:02d}:{mins % 60:02d}"},
    }


def main():
    src, normalized, output_format, positional = _split_argv(sys.argv[1:])
    start, end = parse_args(positional)
    result = run_window(start, end, normalized)
    all_fatal = result.pop("_all_engines_fatal", False)
    if not result["sources"] and src != "auto":
        _die(f"[{src}] 所选来源在本机不可用\n提示: 未找到对应智能体的本地数据目录/库", 2)
    if all_fatal:
        _die("全部所选来源均无法读取（schema 不兼容或数据库损坏）。"
             "\n请确认各智能体客户端版本后重试。", 2)
    if output_format == "markdown":
        print(render_markdown(result))
    else:
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
