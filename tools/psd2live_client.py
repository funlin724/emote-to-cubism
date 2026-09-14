# -*- coding: utf-8 -*-
"""PSD2Live Streamable HTTP MCP 直连客户端（批量写入/读取用）。

端点/Token 来源（与 psd2live 官方 stdio 桥同款逻辑）：
* 端点  = env `PSD2LIVE_MCP_ENDPOINT` 或 http://127.0.0.1:23871/mcp
* Token = env `PSD2LIVE_MCP_TOKEN`，或 Windows 注册表
  HKCU\\Software\\JavaSoft\\Prefs\\io\\github\\psd2live\\agent 的
  agent_mcp_bearer_token（值需按 Java Preferences 的斜杠转义解码）。
  本工具不内嵌任何令牌。

用法::

    python tools/psd2live_client.py state                 # project_get_state
    python tools/psd2live_client.py call <tool> '<json>'  # 调任意工具
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid

try:
    import winreg
except ImportError:  # pragma: no cover
    winreg = None

ENDPOINT = os.environ.get("PSD2LIVE_MCP_ENDPOINT", "http://127.0.0.1:23871/mcp")
TOKEN_ENV = "PSD2LIVE_MCP_TOKEN"


def get_token() -> str | None:
    token = os.environ.get(TOKEN_ENV)
    if token:
        return token
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\JavaSoft\Prefs\io\github\psd2live\agent",
        ) as key:
            raw, _ = winreg.QueryValueEx(key, "agent_mcp_bearer_token")
    except OSError:
        return None
    # Java Preferences 的斜杠转义："/x" → 大写，"//" → "/"
    out: list[str] = []
    i = 0
    while i < len(raw):
        if raw[i] == "/" and i + 1 < len(raw):
            i += 1
            out.append("/" if raw[i] == "/" else raw[i].upper())
        else:
            out.append(raw[i])
        i += 1
    return "".join(out)


class Client:
    def __init__(self, endpoint: str = ENDPOINT, token: str | None = None):
        self.endpoint = endpoint
        self.token = token or get_token()
        self.session: str | None = None
        self.protocol: str | None = None
        self._id = 0

    def _headers(self) -> dict:
        h = {
            "Accept": "application/json, text/event-stream",
            "Authorization": "Bearer %s" % self.token,
            "Content-Type": "application/json",
        }
        if self.session:
            h["Mcp-Session-Id"] = self.session
        if self.protocol:
            h["MCP-Protocol-Version"] = self.protocol
        return h

    def _post(self, payload: dict):
        req = urllib.request.Request(
            self.endpoint, data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(), method="POST")
        with urllib.request.urlopen(req, timeout=120) as r:
            sid = r.headers.get("Mcp-Session-Id")
            if sid:
                self.session = sid
            pv = r.headers.get("MCP-Protocol-Version")
            if pv:
                self.protocol = pv
            ct = r.headers.get("Content-Type", "")
            body = r.read().decode("utf-8", "replace")
        if not body:
            return None
        if "text/event-stream" in ct:
            data = None
            for line in body.splitlines():
                if line.startswith("data:"):
                    data = line[5:].strip()
            return json.loads(data) if data else None
        return json.loads(body)

    def initialize(self):
        self._id += 1
        resp = self._post({
            "jsonrpc": "2.0", "id": str(self._id), "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "emote-to-cubism", "version": "1.0"}},
        })
        if isinstance(resp, dict) and "result" in resp:
            self.protocol = resp["result"].get("protocolVersion", self.protocol)
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return resp

    def call(self, tool: str, arguments: dict):
        self._id += 1
        return self._post({
            "jsonrpc": "2.0", "id": str(self._id), "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        })

    def result_text(self, resp) -> str:
        """tools/call 的 text 结果拼接（isError 时也返回内容）。"""
        if resp is None:
            return ""
        if "error" in resp:
            return "ERROR: %s" % json.dumps(resp["error"], ensure_ascii=False)
        content = resp.get("result", {}).get("content") or []
        return "\n".join(c.get("text", "") for c in content if c.get("type") == "text")


def connect() -> Client:
    c = Client()
    if not c.token:
        raise SystemExit("找不到 PSD2Live MCP Token（注册表/环境变量均无）")
    c.initialize()
    return c


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "state":
        c = connect()
        print(c.result_text(c.call("project_get_state", {})))
    elif len(sys.argv) >= 4 and sys.argv[1] == "call":
        c = connect()
        print(c.result_text(c.call(sys.argv[2], json.loads(sys.argv[3]))))
    else:
        print(__doc__)
