"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");

const READ_ONLY_METHODS = new Set([
  "get_sessions",
  "get_messages_by_message_ids",
  "get_session",
  "get_session_usage",
]);

function fail(message) {
  process.stderr.write(String(message) + "\n");
  process.exitCode = 1;
}

function installTimeout(ms, reject) {
  const timer = setTimeout(() => reject(new Error("Trae IPC 请求超时")), ms);
  timer.unref?.();
  return timer;
}

async function requestOnce(input) {
  if (!READ_ONLY_METHODS.has(input.method)) {
    throw new Error(`方法不在只读白名单: ${input.method || "<empty>"}`);
  }
  if (input.service !== "chat") {
    throw new Error(`不支持的 Trae service: ${input.service || "<empty>"}`);
  }

  const modulePath = path.join(
    input.install_root,
    "resources",
    "app",
    "node_modules",
    "@aha-kit",
    "ipc-win32-x64"
  );
  if (!fs.existsSync(path.join(modulePath, "package.json"))) {
    throw new Error("Trae 安装中缺少 @aha-kit/ipc-win32-x64");
  }

  const { connect, setLogger } = require(modulePath);
  setLogger(null);
  const connection = connect("ai-agent", input.runtime_dir
    ? { runtimeDir: input.runtime_dir }
    : undefined);
  const timeoutMs = Math.max(100, Number(input.timeout_ms) || 5000);
  const rpcId = "1";
  const channelId = crypto.randomUUID();
  const params = {
    service: input.service,
    method: input.method,
    data: JSON.stringify(input.data || {}),
    common_params: {},
    user_info: {
      name: "",
      token: "",
      region: "",
      is_internal: false,
      user_id: "",
      scope: "",
    },
    streamlined_common_params: {},
    client_info: { connect_session_id: "" },
  };
  const packet = {
    packet_type: "request",
    session_id: "",
    channel_id: channelId,
    params,
  };
  const rpcRequest = JSON.stringify({
    jsonrpc: "2.0",
    method: "request",
    id: rpcId,
    params: [packet],
  });

  try {
    return await new Promise((resolve, reject) => {
      const timer = installTimeout(timeoutMs, reject);
      const finish = (error, value) => {
        clearTimeout(timer);
        connection.off("message", onMessage);
        connection.off("error", onError);
        error ? reject(error) : resolve(value);
      };
      const onError = error => finish(new Error(error?.message || error?.code || String(error)));
      const onMessage = raw => {
        let response;
        try {
          response = JSON.parse(Buffer.isBuffer(raw) ? raw.toString("utf8") : String(raw));
        } catch {
          return;
        }
        if (String(response.id) !== rpcId) {
          return;
        }
        if (response.error) {
          finish(new Error(response.error.message || "Trae RPC 返回错误"));
          return;
        }
        const result = response.result;
        if (!result || typeof result !== "object" || !("params" in result)) {
          finish(new Error("Trae RPC 响应缺少 result.params"));
          return;
        }
        finish(null, result.params);
      };
      connection.on("message", onMessage);
      connection.on("error", onError);
      connection.send(rpcRequest);
    });
  } finally {
    connection.disconnect();
  }
}

let input;
try {
  input = JSON.parse(fs.readFileSync(0, "utf8"));
} catch (error) {
  fail(`无效请求 JSON: ${error.message}`);
}

if (input) {
  requestOnce(input)
    .then(result => process.stdout.write(JSON.stringify(result) + "\n"))
    .catch(error => fail(error.message || error));
}
