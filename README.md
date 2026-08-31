# cowork-recall（人机共事回忆）

> 一句话：**你和 AI 共同做的每一件事，都值得被找回**——跨 **13 款** AI 编码智能体检索任何一次旧会话，并一键生成可信的日报 / 周报。

![skills.sh](https://skills.sh/b/5iFish/cowork-recall-skill)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)
![Python](https://img.shields.io/badge/Python-3.8%2B-blue.svg)
![Platform](https://img.shields.io/badge/平台-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)

---

## ✨ 为什么需要它

和 AI 结对编程的一天是这样的：上午用 Claude Code 改 bug，下午让 Codex 写脚本，晚上用 ZCode 调 UI，中间还穿插着 Cline、Gemini、Trae……

于是每到周五你都在问自己三个问题：

1. 「上周那个登录重构方案，到底是在**哪个会话**里聊的？」
2. 「当时和 AI 讨论的**结论**是什么来着？」
3. 「这周周报……我到底干了啥？」

**cowork-recall** 只读访问本机 13 个 AI 编码智能体的持久化会话，把散落各 App 的对话变成一座可检索的记忆库：

- 🔍 **会话检索（search）**：按关键词跨 App 搜索会话，命中标题 / 提问 / 项目目录，带时间和命中片段
- 📜 **历史浏览（list）**：按时间范围分页浏览全部会话，跨 App 合并、新→旧排序
- 💬 **会话详情（detail）**：还原指定会话的完整对话脉络，提炼当时的结论与待办
- 📊 **工作总结（summary）**：任意时段的日报 / 周报，交叉核对 git 提交，附 Token 用量统计

> **cowork**（和 AI 共同工作）+ **recall**（找回）——你与 AI 的共事记录，不止被总结，更可以被随时回忆。

### 效果预览

**找回一次旧讨论：**

```text
你：之前哪个会话讨论过登录重构？

[claude] [08-25 14:20–16:05] 登录页重构方案讨论
  命中："…把登录重构拆成两期，先做会话过期…"
[zcode]  [08-25 16:40–17:55] 登录重构第一期实现
  命中："…按昨天定的方案先改 token 刷新…"
```

**生成一份周报：**

```markdown
# 工作总览（2026-08-20 ~ 2026-08-26）

- atlas-web：完成登录重构方案讨论与第一期落地（claude + zcode）
- ZCodeProject：发布 cowork-recall 技能（zcode）

# 分项目明细

## atlas-web
[claude]  [08-25 14:20–16:05] 登录页重构方案讨论
- 把登录重构拆成两期，先做会话过期
- commit: `a1b2c3d 重构登录会话过期逻辑`

# Token 用量统计（按模型，按来源分组）

### zcode
| 模型 | 请求数 | 输入 | 输出 | 缓存读 | 总计 | 工具调用 |
|---|---|---|---|---|---|---|
| kimi-k3 | 42 | 1.2M | 38K | 890K | 2.1M | 156 |

# 备注
- 未检测到 codex 本地数据
```

## 🚀 安装

> 前置要求：**Python 3.8+**（仅需标准库，零三方依赖）。

```bash
npx skills add 5iFish/cowork-recall-skill
```

也可以只试用不安装：

```bash
npx skills use 5iFish/cowork-recall-skill | claude
```

指定安装目标（默认会交互式询问）：

```bash
# 全局安装到 Claude Code
npx skills add 5iFish/cowork-recall-skill -g -a claude-code -y

# 安装到多个智能体
npx skills add 5iFish/cowork-recall-skill -g -a claude-code -a codex -a cursor
```

## 🛠️ 使用方法

安装完成后，直接用自然语言对 AI 说：

| 你想说的 | 它做什么 |
|---|---|
| 「之前哪个会话讨论过 X？」 | search 跨 App 检索会话 |
| 「看看我最近的会话历史」 | list 分页浏览 |
| 「那个会话当时的结论是什么？」 | detail 还原完整对话并提炼结论 |
| 「总结一下我今天干了什么」 | 生成今日工作总结 |
| 「本周工作总结」/「2026-08-20 到 08-26 的周报」 | 生成任意时段总结 |

它会自动完成：理解意图 → 检索/提取各引擎会话 → git 交叉核对 → 按固定模板成文。

## 📡 支持的来源（13 个）

| 来源 | `--source` | 产品 |
|---|---|---|
| ZCode | `zcode` | ZCode |
| Claude Code | `claude` | Claude Code |
| Codex | `codex` | OpenAI Codex CLI |
| Gemini CLI | `gemini` | Gemini CLI |
| Cline | `cline` | Cline（含 legacy tasks） |
| Roo Code | `roo` | Roo Code |
| Continue | `continue` | Continue |
| OpenCode | `opencode` | OpenCode（SQLite + 旧版 JSON） |
| Qoder | `qoder` | Qoder 国际版 / CN Desktop |
| WorkBuddy | `workbuddy` | WorkBuddy |
| CodeBuddy | `codebuddy` | CodeBuddy |
| Kimi Code | `kimi` | Kimi Code |
| Trae | `trae` | Trae / Trae CN（**需 Trae 处于运行中**，经其本地服务只读获取） |

来源默认 `auto` 自动检测；可用 `WORKSUMMARY_<X>_*` 系列环境变量覆盖各来源的数据位置。某来源数据不存在时如实降级，不影响其他来源。会话详情（detail）目前支持 zcode / claude / codex / trae，其余来源以检索摘要为准。

> ⚠️ **Trae 特殊说明**：Trae / Trae CN 的会话**必须在 Trae 处于运行中时才能读取**——适配器通过连接运行中的 Trae 本地 ai-agent 服务来只读获取会话。查询 Trae 会话前，请先启动并保持 Trae / Trae CN 运行，否则该来源会被如实标记为不可用（其余来源不受影响）。

## 🔒 数据与隐私

- 全部数据**只在本机读取**，SQLite 始终只读打开，不写入任何智能体的会话或配置。
- **零网络请求**，不读取云端账户，总结在本地生成。
- 同一会话存在 SQLite、JSONL 或迁移副本时合并去重，不重复累计 Token。
- 只有可靠 usage 字段才进入 Token 统计；代码索引、上下文额度等不会误计。

## ⚙️ 进阶：直接运行脚本

不经过 AI 也可以直接使用：

```bash
# 检索：找会话 / 翻历史 / 看详情
python skills/cowork-recall/scripts/session_recall.py search --query "登录重构"
python skills/cowork-recall/scripts/session_recall.py list 2026-08-20 2026-08-26
python skills/cowork-recall/scripts/session_recall.py detail --source claude --session <会话ID>

# 总结：JSON 或 Markdown
python skills/cowork-recall/scripts/work_summary.py 2026-08-27              # 单日
python skills/cowork-recall/scripts/work_summary.py --format markdown 2026-08-20 2026-08-26  # 周报
python skills/cowork-recall/scripts/work_summary.py --source claude 2026-08-27  # 只看 Claude Code
```

退出码：`0` 正常；`1` 参数错误；`2` 所选来源全部不可用；`3`（仅 detail）会话未找到。

## ❓ 常见问题

**Q: 会泄露我的代码或会话隐私吗？**
不会。所有数据只在本机读取，没有任何网络请求。

**Q: 需要安装什么依赖吗？**
只需要 Python 3.8+，全部使用标准库。

**Q: 我的智能体不在支持列表里？**
欢迎提 Issue 或 PR。适配新引擎只需在 `scripts/adapters/` 下新增一个适配器（参考现有实现，实现统一接口即可）。

## 📂 仓库结构

```
skills/cowork-recall/
├── SKILL.md                    # 技能入口（能力路由与执行流程）
└── scripts/
    ├── session_recall.py       # 检索入口：search / list / detail
    ├── work_summary.py         # 总结入口：任意时段工作总结 + Token 统计
    ├── markdown_renderer.py    # 总结成文渲染
    ├── install_skill.py        # 可选：链接/复制安装到 ~/.agents/skills
    └── adapters/               # 13 个引擎的数据适配器（统一接口）
```

## 🤝 贡献

欢迎 Issue 与 PR！如果你的改动涉及提示词或脚本逻辑，请附带实际的检索 / 总结输出样例。

贡献者：ZCode · GLM · GPT · Kimi

## 📄 License

[MIT](LICENSE)

## 友情链接

- [linux.do](https://linux.do/u/runtimexception/)
