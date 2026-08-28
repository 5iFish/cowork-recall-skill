"""Trae 国际版/CN 运行时本地 RPC 适配器。"""
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess

from .common import (MSG_CAP, PROMPT_CAP, SESSION_CAP, TEXT_CAP,
                     clean_text, fmt)

SOURCE = "trae"
DEFAULT_PAGE_SIZE = 30


@dataclass
class VariantSpec:
    variant: str
    root: str
    transport: object = None
    install_root: str = ""


def _home():
    return Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))


def variant_specs():
    appdata = Path(os.environ.get("APPDATA") or _home() / "AppData" / "Roaming")
    return [
        VariantSpec(
            "trae_ide",
            os.environ.get("WORKSUMMARY_TRAE_DIR") or str(appdata / "Trae"),
            install_root=os.environ.get("WORKSUMMARY_TRAE_INSTALL") or
            r"D:\Software\Trae",
        ),
        VariantSpec(
            "trae_cn_ide",
            os.environ.get("WORKSUMMARY_TRAE_CN_DIR") or str(appdata / "Trae CN"),
            install_root=os.environ.get("WORKSUMMARY_TRAE_CN_INSTALL") or
            r"D:\Software\Trae CN",
        ),
    ]


def _ms(value):
    if isinstance(value, (int, float)):
        n = int(value)
        return n * 1000 if abs(n) < 100_000_000_000 else n
    if isinstance(value, str):
        try:
            return _ms(float(value))
        except ValueError:
            try:
                return int(datetime.fromisoformat(value.replace("Z", "+00:00"))
                           .timestamp() * 1000)
            except ValueError:
                return None
    return None


def _texts(content):
    if isinstance(content, str):
        text = clean_text(content)
        return [text] if text else []
    if isinstance(content, dict):
        for key in ("content", "text", "message"):
            if key in content:
                return _texts(content[key])
        return []
    if isinstance(content, list):
        out = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") in ("tool_use", "function_call", "tool_result"):
                continue
            out.extend(_texts(item.get("text") if "text" in item else item.get("content")))
        return out
    return []


def _messages_inline(session):
    messages = session.get("messages")
    return messages if isinstance(messages, list) else []


def _message_ids(session):
    raw = session.get("message_ids") or session.get("messageIds") or []
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    return []


def _external_id(variant, raw_id):
    return f"{variant}:{raw_id}"


def _split_external_id(session_id):
    for variant in ("trae_ide", "trae_cn_ide"):
        prefix = variant + ":"
        if session_id.startswith(prefix):
            return variant, session_id[len(prefix):]
    return None, session_id


def _session_time(session, *keys):
    for key in keys:
        value = _ms(session.get(key))
        if value is not None:
            return value
    return None


def _session_path(session):
    for key in ("project_path", "workspace_path", "worktree_path", "cwd", "directory"):
        value = session.get(key)
        if isinstance(value, str) and value:
            return value
    project = session.get("project") or {}
    if isinstance(project, dict):
        return project.get("path") or project.get("directory") or ""
    return ""


def _role(message):
    role = str(message.get("role") or message.get("message_role") or "").lower()
    if role in ("user", "human"):
        return "user"
    if role in ("assistant", "ai"):
        return "assistant"
    return None


def _message_content(message):
    for key in ("content", "text", "message"):
        if key in message:
            return _texts(message[key])
    return []


def _prompts(messages):
    prompts = []
    for message in messages:
        if not isinstance(message, dict) or _role(message) != "user":
            continue
        for text in _message_content(message):
            if len(text) >= 4 and text not in prompts:
                prompts.append(text)
                if len(prompts) >= PROMPT_CAP:
                    return prompts
    return prompts


def _response_data(response):
    if not isinstance(response, dict):
        raise ValueError("RPC response is not an object")
    if "code" in response and response.get("code") not in (0, None):
        raise ValueError(response.get("message") or f"RPC code {response['code']}")
    data = response.get("data")
    return data if isinstance(data, dict) else response


def _request(transport, method, data):
    return _response_data(transport.request("chat", method, data))


def _fetch_messages(transport, session):
    inline = _messages_inline(session)
    if inline:
        return inline
    ids = _message_ids(session)
    if not ids:
        return []
    data = _request(transport, "get_messages_by_message_ids", {"message_ids": ids})
    messages = data.get("messages")
    if not isinstance(messages, list):
        raise ValueError("get_messages_by_message_ids 缺少 messages")
    return messages


def _session_record(spec, session, messages):
    raw_id = str(session.get("session_id") or session.get("sessionId") or "")
    created = _session_time(session, "created_at", "createdAt", "create_at")
    updated = _session_time(session, "update_at", "updated_at", "updatedAt") or created
    if not raw_id or created is None:
        raise ValueError("会话缺少 session_id/created_at")
    prompts = _prompts(messages)
    title = clean_text(session.get("title") or session.get("name") or "")
    if not title and prompts:
        title = prompts[0][:60]
    local = datetime.fromtimestamp(created / 1000).astimezone()
    end = datetime.fromtimestamp(updated / 1000).astimezone()
    return {
        "source": SOURCE, "variant": spec.variant, "backend": "trae_ai_agent_rpc",
        "parent_session_id": session.get("parent_session_id"),
        "is_subagent": bool(session.get("is_subagent")),
        "session_id": _external_id(spec.variant, raw_id),
        "day": local.strftime("%Y-%m-%d"), "start": local.strftime("%H:%M"),
        "end": end.strftime("%H:%M"), "_order": local.strftime("%Y-%m-%d%H:%M"),
        "title": title, "dir": _session_path(session), "prompts": prompts,
    }


def _num(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _usage(transport, messages, model_id, seen, models):
    for message in messages:
        if not isinstance(message, dict) or _role(message) != "assistant":
            continue
        mid = str(message.get("message_id") or message.get("messageId") or "")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        data = _request(transport, "get_session_usage", {"message_id": mid})
        token = data.get("token") or data.get("tokens")
        if not isinstance(token, dict):
            continue
        inp = _num(token.get("input") or token.get("input_tokens"))
        out = _num(token.get("output") or token.get("output_tokens"))
        cache = _num(token.get("cache_read") or token.get("cache_read_tokens"))
        reasoning = _num(token.get("reasoning") or token.get("reasoning_tokens"))
        total = _num(token.get("total") or token.get("total_tokens")) or inp + out + reasoning
        if not total:
            continue
        row = models.setdefault(model_id, {
            "model_id": model_id, "requests": 0, "in": 0, "out": 0,
            "cache_read": 0, "reasoning": 0, "total": 0,
            "tool_calls": None, "duration_ms": None,
        })
        row["requests"] += 1
        row["in"] += inp
        row["out"] += out
        row["cache_read"] += cache
        row["reasoning"] += reasoning
        row["total"] += total


def _available_specs(specs, degradations):
    out = []
    for spec in specs:
        if spec.transport is None:
            spec.transport = discover_transport(spec)
        if spec.transport is None:
            degradations.append(
                f"[{SOURCE}/{spec.variant}] 客户端未运行，无法读取加密会话库")
            continue
        out.append(spec)
    return out


def gather(start_ts_ms, end_ts_ms, degradations, variants=None,
           page_size=DEFAULT_PAGE_SIZE):
    result = {"sessions": [], "stats_block": None, "truncated": False}
    specs = _available_specs(list(variants) if variants is not None else variant_specs(),
                             degradations)
    models, seen_usage = {}, set()
    for spec in specs:
        offset, seen_sessions = 0, set()
        while len(seen_sessions) < SESSION_CAP:
            try:
                data = _request(spec.transport, "get_sessions",
                                {"limit": page_size, "offset": offset})
                sessions = data.get("sessions")
                if not isinstance(sessions, list):
                    raise ValueError("get_sessions 缺少 sessions")
            except (OSError, ValueError, TimeoutError) as exc:
                degradations.append(f"[{SOURCE}/{spec.variant}] RPC 提取失败: {exc}")
                break
            if not sessions:
                break
            for session in sessions:
                if not isinstance(session, dict):
                    continue
                raw_id = str(session.get("session_id") or session.get("sessionId") or "")
                if not raw_id or raw_id in seen_sessions:
                    continue
                seen_sessions.add(raw_id)
                created = _session_time(session, "created_at", "createdAt", "create_at")
                if created is None or not start_ts_ms <= created < end_ts_ms:
                    continue
                try:
                    messages = _fetch_messages(spec.transport, session)
                    record = _session_record(spec, session, messages)
                    result["sessions"].append(record)
                    model = str(session.get("model") or session.get("model_id") or "unknown")
                    _usage(spec.transport, messages, model, seen_usage, models)
                except (OSError, ValueError, TimeoutError) as exc:
                    degradations.append(
                        f"[{SOURCE}/{spec.variant}] 会话 {raw_id} 读取失败: {exc}")
            if len(sessions) < page_size:
                break
            offset += len(sessions)
        if len(seen_sessions) >= SESSION_CAP:
            result["truncated"] = True
    rows = sorted(models.values(), key=lambda item: -item["total"])
    for row in rows:
        row.update(in_h=fmt(row["in"]), out_h=fmt(row["out"]),
                   total_h=fmt(row["total"]))
    if rows:
        result["stats_block"] = {
            "models": rows, "grand_total": sum(row["total"] for row in rows),
            "nonmain_total": 0,
        }
    return result


def fetch_detail(session_id, degradations, variants=None):
    variant, raw_id = _split_external_id(session_id)
    specs = list(variants) if variants is not None else variant_specs()
    if variant:
        specs = [spec for spec in specs if spec.variant == variant]
    specs = _available_specs(specs, degradations)
    matches = []
    for spec in specs:
        offset = 0
        while offset < SESSION_CAP:
            try:
                data = _request(spec.transport, "get_sessions",
                                {"limit": DEFAULT_PAGE_SIZE, "offset": offset})
                sessions = data.get("sessions")
                if not isinstance(sessions, list):
                    raise ValueError("get_sessions 缺少 sessions")
            except (OSError, ValueError, TimeoutError) as exc:
                degradations.append(f"[{SOURCE}/{spec.variant}] RPC 提取失败: {exc}")
                break
            hit = next((s for s in sessions if str(s.get("session_id") or
                       s.get("sessionId") or "") == raw_id), None)
            if hit:
                matches.append((spec, hit))
                break
            if len(sessions) < DEFAULT_PAGE_SIZE:
                break
            offset += len(sessions)
    if len(matches) != 1:
        if len(matches) > 1:
            degradations.append(f"[{SOURCE}] 无前缀 session_id 在多个 Trae variant 中有歧义")
        return {"found": False}
    spec, session = matches[0]
    try:
        messages = _fetch_messages(spec.transport, session)
    except (OSError, ValueError, TimeoutError) as exc:
        degradations.append(f"[{SOURCE}/{spec.variant}] 消息读取失败: {exc}")
        return {"found": False}
    output = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = _role(message)
        if role not in ("user", "assistant"):
            continue
        ts_ms = _session_time(message, "created_at", "timestamp", "create_at")
        hhmm = (datetime.fromtimestamp(ts_ms / 1000).astimezone().strftime("%H:%M")
                if ts_ms is not None else "")
        for text in _message_content(message):
            if len(output) >= MSG_CAP:
                break
            output.append({"ts": hhmm, "role": role, "text": text[:TEXT_CAP]})
        if len(output) >= MSG_CAP:
            break
    output.sort(key=lambda item: item["ts"])
    record = _session_record(spec, session, messages)
    record.pop("_order", None)
    return {"found": True, "session": record, "messages": output,
            "truncated": len(output) >= MSG_CAP}


READ_ONLY_METHODS = frozenset({
    "get_sessions",
    "get_messages_by_message_ids",
    "get_session",
    "get_session_usage",
})


class NativeTransport:
    """通过 Trae 自带 Aha IPC 客户端执行单次只读 RPC。"""

    def __init__(self, root, variant, timeout=5, runner=subprocess.run,
                 node_command="node", runtime_dir=None):
        self.root = str(root)
        self.variant = variant
        self.timeout = timeout
        self.runner = runner
        self.node_command = node_command
        self.runtime_dir = runtime_dir
        self.bridge = str(Path(__file__).with_name("trae_ipc_bridge.js"))

    def request(self, service, method, data):
        if method not in READ_ONLY_METHODS:
            raise ValueError(f"方法不在只读白名单: {method}")
        payload = {
            "install_root": self.root,
            "variant": self.variant,
            "runtime_dir": self.runtime_dir,
            "service": service,
            "method": method,
            "data": data,
            "timeout_ms": int(self.timeout * 1000),
        }
        try:
            completed = self.runner(
                [self.node_command, self.bridge],
                input=json.dumps(payload, ensure_ascii=False),
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=self.timeout + 2,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OSError(f"Trae IPC bridge 启动失败: {exc}") from exc
        if completed.returncode != 0:
            message = (completed.stderr or "Trae IPC bridge 失败").strip()
            raise OSError(message[:500])
        try:
            response = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Trae IPC bridge 返回无效 JSON") from exc
        if not isinstance(response, dict):
            raise ValueError("Trae IPC bridge 响应不是对象")
        return response

    def identify(self):
        try:
            data = _request(self, "get_sessions", {"limit": 1, "offset": 0})
        except (OSError, ValueError, TimeoutError):
            return False
        return isinstance(data.get("sessions"), list)


def discover_transport(spec):
    """验证本地数据根与运行中的 Trae ai-agent 后返回只读 transport。"""
    if not Path(spec.root).is_dir():
        return None
    install_root = spec.install_root or spec.root
    transport = NativeTransport(install_root, spec.variant, timeout=1.5)
    return transport if transport.identify() else None
