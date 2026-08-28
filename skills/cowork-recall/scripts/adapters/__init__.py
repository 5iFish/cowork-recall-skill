"""适配器注册表：统一接口 gather(start_ms,end_ms,degradations)->body。

body = {sessions:[SessionRecord], stats_block|None, truncated}
engine 隔离规则：任一 WORKSUMMARY_<X> 显式 env 存在 => 仅启用被显式提供的引擎；
否则启用所有默认位置存在的引擎。
"""
import importlib
import os

ORDER = [
    "zcode", "claude_code", "codex", "gemini", "cline", "roo_code",
    "continue", "opencode", "qoder", "workbuddy", "codebuddy", "kimi_code",
    "trae",
]

ENV_KEYS = {
    "zcode": ("WORKSUMMARY_ZCODE_DB", "ZCODE_DB_PATH"),
    "claude_code": ("WORKSUMMARY_CLAUDE_DIR",),
    "codex": ("WORKSUMMARY_CODEX_DIR",),
    "gemini": ("WORKSUMMARY_GEMINI_DIR",),
    "cline": ("WORKSUMMARY_CLINE_DIR", "WORKSUMMARY_CLINE_DB", "WORKSUMMARY_CLINE_LEGACY_DIR"),
    "roo_code": ("WORKSUMMARY_ROO_DIR",),
    "continue": ("WORKSUMMARY_CONTINUE_DIR",),
    "opencode": ("WORKSUMMARY_OPENCODE_DIR", "WORKSUMMARY_OPENCODE_DB"),
    "qoder": ("WORKSUMMARY_QODER_DIR", "WORKSUMMARY_QODER_CN_DIR",
              "WORKSUMMARY_QODER_IDE_DB", "WORKSUMMARY_QODER_CN_IDE_DB"),
    "workbuddy": ("WORKSUMMARY_WORKBUDDY_DIR", "WORKSUMMARY_WORKBUDDY_DB"),
    "codebuddy": ("WORKSUMMARY_CODEBUDDY_DIR",),
    "kimi_code": ("WORKSUMMARY_KIMI_DIR",),
    "trae": ("WORKSUMMARY_TRAE_DIR", "WORKSUMMARY_TRAE_CN_DIR"),
}

MODULES = {"continue": "continue_ai"}


def home():
    return os.environ.get("USERPROFILE") or os.path.expanduser("~")


def default_paths():
    h = home()
    appdata = os.environ.get("APPDATA") or os.path.join(h, "AppData", "Roaming")
    return {
        "zcode": [os.path.join(h, ".zcode", "cli", "db", "db.sqlite")],
        "claude_code": [os.path.join(h, ".claude", "projects")],
        "codex": [os.path.join(h, ".codex", "sessions")],
        "gemini": [os.path.join(h, ".gemini", "tmp")],
        "cline": [os.path.join(h, ".cline", "data", "db", "sessions.db"),
                  os.path.join(appdata, "Code", "User", "globalStorage", "saoudrizwan.claude-dev")],
        "roo_code": [os.path.join(appdata, "Code", "User", "globalStorage", "rooveterinaryinc.roo-cline")],
        "continue": [os.path.join(h, ".continue", "sessions")],
        "opencode": [os.path.join(h, ".local", "share", "opencode", "opencode.db")],
        "qoder": [os.path.join(h, ".qoder"), os.path.join(h, ".qoder-cn"),
                  os.path.join(appdata, "com.qoder.app.stable", "main.sqlite"),
                  os.path.join(appdata, "com.qodercn.app.stable", "main.sqlite"),
                  os.path.join(appdata, "Qoder", "User", "globalStorage", "state.vscdb"),
                  os.path.join(appdata, "QoderCN", "User", "globalStorage", "state.vscdb")],
        "workbuddy": [os.path.join(h, ".workbuddy")],
        "codebuddy": [os.path.join(h, ".codebuddy", "projects")],
        "kimi_code": [os.path.join(h, ".kimi-code", "session_index.jsonl")],
        "trae": [os.path.join(appdata, "Trae"), os.path.join(appdata, "Trae CN")],
    }


def detect_sources():
    """返回本轮启用的引擎名列表（保持 ORDER 次序）。"""
    dp = default_paths()
    explicit = []
    for name, keys in ENV_KEYS.items():
        for k in keys:
            v = os.environ.get(k)
            if v and os.path.exists(v):
                explicit.append(name)
                break
            if v:                       # env 给了但路径不存在：也算显式意图，交由引擎报错
                explicit.append(name)
                break
    if explicit:
        return [n for n in ORDER if n in explicit]
    found = []
    for n in ORDER:
        if any(os.path.exists(p) for p in dp[n]):
            found.append(n)
    return found


def get_adapter(name):
    if name not in ORDER:
        raise KeyError(name)
    return importlib.import_module(f"{__name__}.{MODULES.get(name, name)}")
