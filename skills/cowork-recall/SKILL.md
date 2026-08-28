---
name: cowork-recall
description: 跨 AI 编码智能体（ZCode、Claude Code、Codex、Gemini CLI、Cline、Roo Code、Continue、OpenCode、Qoder、WorkBuddy、CodeBuddy、Kimi Code、Trae）的本机会话检索与回忆中心。按关键词或时间范围跨 App 搜索相关会话、分页浏览聚合会话历史、查看指定会话的完整对话与讨论结论，并在此基础上生成每日/每周/任意时段的工作总结。当用户问"之前哪个会话讨论过 X""上周和 AI 聊了什么""总结今天做了什么""生成日报/周报"时使用。
---

# cowork-recall 跨 App 会话检索与工作总结

核心能力是**跨 App 会话内容检索**：统一只读访问本机 13 个 AI 编码智能体的持久化会话，支持内容搜索、历史浏览、会话详情；每日/每周/时段工作总结是检索之上的成文能力。

## 能力路由

| 用户意图 | 子命令 |
|---|---|
| "之前哪个会话讨论过 X" / "找一下关于 X 的会话" | `search` |
| "看看我最近的会话历史" / "上周都聊了些什么" | `list` |
| "这个会话当时结论是什么" / "展开看看这个会话" | `detail` |
| "总结今天/本周的工作" / "生成日报/周报" | `summary`（work_summary.py） |

组合场景：先 `search` 定位相关会话（含时间、来源 App、命中内容），再用 `detail` 查看指定会话详情并提炼当时结论。

## 0. 通用约定

依次尝试 `python3`、`python`、`py -3`；下文以 `python` 示意。`<技能根目录>` 为本 SKILL.md 所在目录。

**时间范围**（search/list/summary 通用）：

- `YYYY-MM-DD` 为单日；`A~B`、`A至B`、`A到B` 换算为两个位置参数的闭区间。
- search 无参默认最近 30 天；list 无参默认最近 7 天；summary 无参默认当天。
- 昨天/本周/本月/最近 N 天按当前环境日期推算；非法日期或起晚于止时说明用法，不运行脚本。

**来源过滤**：`--source auto|all|zcode|claude|codex|gemini|cline|roo|continue|opencode|qoder|workbuddy|codebuddy|kimi|trae`，默认 `auto`（自动检测本机全部来源）。

**分页**：`--page N`（从 1 开始，默认 1）、`--page-size M`（1–100，默认 20）。输出含 `total/page/page_size/has_more`；`has_more=true` 时应询问或直接取下一页，不得宣称"已列出全部"。

退出码：0 成功（部分后端失败写入 `degradations`）；1 参数错误；2 来源不可用或致命 schema 不兼容；3（仅 detail）会话未找到。

显式设置任一 `WORKSUMMARY_*` 根后，只启用显式来源，避免测试夹具混入本机数据。

## 1. 跨 App 内容检索（search）

```bash
python "<技能根目录>/scripts/session_recall.py" search --query "<关键词>" \
  [YYYY-MM-DD [YYYY-MM-DD]] [--source ...] [--page N] [--page-size M]
```

- 关键词按空格拆为多个词项，在会话**标题、用户提问、项目目录**中做不区分大小写的命中（任一词项命中即算）。
- 每条结果含 `source/variant`（来源 App）、`day/start/end`（会话时间）、`title`、`dir`、`session_id`、`prompts`、`matches`（命中片段），按时间新→旧排序。
- 呈现时按来源 App 或按日期分组列出：`[source/variant] [day HH:MM–HH:MM] 标题`，下接命中片段。
- 结果过多时先给第 1 页并告知 `total` 与翻页方式。

## 2. 分页浏览聚合历史（list）

```bash
python "<技能根目录>/scripts/session_recall.py" list \
  [YYYY-MM-DD [YYYY-MM-DD]] [--source ...] [--page N] [--page-size M]
```

- 输出指定时间范围内全部来源的会话，跨 App 合并、按时间新→旧排序。
- 每条含来源 App、时间、标题、目录、`session_id` 和前若干条提问要点。
- 用于"回顾某时段都做了什么/讨论了哪些话题"，也是进入 `detail` 的入口。

## 3. 会话详情（detail）

```bash
python "<技能根目录>/scripts/session_recall.py" detail \
  --source <search/list 结果中的 source> --session <session_id>
```

- 返回该会话的元信息（来源、时间、标题、目录）和按时间升序的完整 `messages`（`ts/role/text`，含 user 与 assistant），以及 `message_count/truncated`。
- 用户问"当时讨论的结论"时：取 detail 输出，重点读靠后的 assistant 消息，用 3–6 点提炼结论与待办，并注明会话时间与来源 App；`truncated=true` 时说明只覆盖前 200 条消息。
- 目前支持 detail 的来源：`zcode`、`claude`（claude_code）、`codex`、`trae`。其余来源退出码 2 并提示改用 search/list 摘要——此时直接基于 search/list 的标题与提问回答，不要重试 detail。
- detail 的 `--source`/`--session` 必须取自 search/list 的输出，不得臆造 session_id。

## 4. 工作总结（summary）

```bash
python "<技能根目录>/scripts/work_summary.py" \
  [--source ...] [--format json|markdown] <start> [<end>]
```

JSON v3 包含 `window/sources/sessions/stats.by_source/degradations/truncated/meta`；每条 session 含 `source/variant/backend/session_id/day/start/end/title/dir/prompts/parent_session_id/is_subagent`；`stats.by_source.<source>` 含 `models/grand_total/nonmain_total`。

**Git 交叉核对**：对 `sessions[].dir` 去重后运行只读命令：

```bash
git -C "<dir>" log --since="<start>T00:00:00" --until="<next-of-end>T00:00:00" --pretty=format:"%h%x09%ad%x09%s" --date=format:"%H:%M"
```

目录不存在、不是 Git 仓库或命令失败时记入备注；绝不执行 Git 写命令。

**成文**：用 `--format markdown` 或基于脚本 JSON + git 输出生成固定四段：

1. `# 工作总览（<范围>）`：按项目合并 3–6 点。
2. `# 分项目明细`：会话行使用 `[source/variant] [HH:MM–HH:MM] 标题`，下接最多 3 条 prompt 要点和真实 commit。
3. `# Token 用量统计（按模型，按来源分组）`：只展示有可靠模型数据的来源；全为 null 的列省略。
4. `# 备注`：degradations、跳过目录、截断和无 usage 说明。

## 支持边界

- detail 仅 zcode / claude_code / codex / trae；其余来源以 search/list 摘要为准。
- OpenCode 同时支持当前 SQLite 和旧版 JSON；Cline 当前库与 legacy tasks 同 ID 合并。
- Qoder 国际/CN Desktop 的 DB 与 JSONL 合并；IDE、VS Code、CLI、JetBrains 只有确认可靠会话正文后才启用。
- Trae（国际版 / CN）的完整会话通过正在运行的 Trae 本地服务只读获取（IPC 桥），不解密、不复制、不修改其数据库。
- Qoder vector/index/graph/RepoWiki、WorkBuddy 上下文额度、Kimi `token_counting.*` 和 `llm.tools_snapshot` 不算聊天 Token。
- Gemini VS Code Companion 不作为独立会话来源；CodeBuddy 与 WorkBuddy 是独立 source。
- 单来源窗口会话上限 120 条（超出置 `truncated`），摘要提问上限 6 条/会话，详情消息上限 200 条、单条 2000 字符。

**红线：只能依据脚本输出和只读 git log 成文，不得虚构会话、提交、结论、模型或数字。**
