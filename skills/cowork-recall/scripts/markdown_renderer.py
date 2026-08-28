"""将工作总结标准化结果渲染为 Markdown。"""
from collections import defaultdict


def _esc(value):
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def _label(session):
    source = session.get("source") or "unknown"
    variant = session.get("variant") or source
    return source if variant == source else f"{source}/{variant}"


def render_markdown(result):
    window = result.get("window") or {}
    start, end = window.get("start", ""), window.get("end", "")
    sessions = result.get("sessions") or []
    lines = [f"# 工作总览（{start} 至 {end}）"]

    projects = defaultdict(list)
    for session in sessions:
        projects[session.get("dir") or "未标记项目"].append(session)
    if projects:
        for project, items in list(projects.items())[:6]:
            sources = "/".join(dict.fromkeys(i.get("source", "unknown") for i in items))
            title = items[-1].get("title") or "未命名会话"
            lines.append(f"- {_esc(project)}：{_esc(title)}（{sources}）")
    else:
        lines.append("- 指定时间范围内未发现可汇总的本地会话。")

    lines.extend(["", "# 分项目明细"])
    if not projects:
        lines.append("无会话明细。")
    for project, items in projects.items():
        lines.extend([f"## {_esc(project)}"])
        for session in sorted(items, key=lambda s: (s.get("day", ""), s.get("start", ""))):
            lines.append(f"[{_label(session)}] [{session.get('start', '')}–{session.get('end', '')}] "
                         f"{_esc(session.get('title') or '未命名会话')}")
            for prompt in (session.get("prompts") or [])[:3]:
                lines.append(f"- {_esc(prompt)}")

    lines.extend(["", "# Token 用量统计（按模型，按来源分组）"])
    by_source = (result.get("stats") or {}).get("by_source") or {}
    shown = False
    for source, block in by_source.items():
        models = block.get("models") or []
        if not models:
            continue
        shown = True
        lines.append(f"### {source}")
        show_reasoning = any((m.get("reasoning") or 0) for m in models)
        show_tools = any(m.get("tool_calls") is not None for m in models)
        show_duration = any(m.get("duration_ms") is not None for m in models)
        headers = ["模型", "请求数", "输入", "输出", "缓存读"]
        if show_reasoning:
            headers.append("推理")
        headers.append("总计")
        if show_tools:
            headers.append("工具调用")
        if show_duration:
            headers.append("耗时(ms)")
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        for model in models:
            row = [_esc(model.get("model_id")), str(model.get("requests") or 0),
                   str(model.get("in_h", model.get("in", 0))),
                   str(model.get("out_h", model.get("out", 0))),
                   str(model.get("cache_read") or 0)]
            if show_reasoning:
                row.append(str(model.get("reasoning") or 0))
            row.append(str(model.get("total_h", model.get("total", 0))))
            if show_tools:
                row.append("—" if model.get("tool_calls") is None else str(model.get("tool_calls")))
            if show_duration:
                row.append("—" if model.get("duration_ms") is None else str(model.get("duration_ms")))
            lines.append("| " + " | ".join(row) + " |")
        nonmain = block.get("nonmain_total") or 0
        if nonmain:
            lines.append(f"其中非主轮消耗：{nonmain} tokens")
    if not shown:
        lines.append("没有可靠的 Token 用量数据。")

    lines.extend(["", "# 备注"])
    notes = list(result.get("degradations") or [])
    notes.extend(f"跳过 Git 目录：{p}" for p in (result.get("skipped_git_dirs") or []))
    if result.get("truncated"):
        notes.append("结果达到扫描上限，已截断。")
    if notes:
        lines.extend(f"- {_esc(note)}" for note in notes)
    else:
        lines.append("- 无。")
    return "\n".join(lines)
