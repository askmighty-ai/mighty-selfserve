#!/usr/bin/env python3
"""
Mighty MCP Server — stdlib only, no pip install required.

Configuration — add to ~/Library/Application Support/Claude/claude_desktop_config.json:
{
  "mcpServers": {
    "mighty": {
      "command": "python3",
      "args": ["/path/to/mighty_mcp.py"],
      "env": {
        "MIGHTY_API_KEY": "mk_your_key_here",
        "MIGHTY_BASE_URL": "https://your-app.up.railway.app"
      }
    }
  }
}
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

# ── Config ────────────────────────────────────────────────────────────────────

API_KEY  = os.environ.get("MIGHTY_API_KEY", "mk_7b83acc522f784495dc65b2563624dbe4b57b05d")
BASE_URL = os.environ.get("MIGHTY_BASE_URL", "https://mighty-selfserve-production.up.railway.app")

POLL_INTERVAL = 3    # seconds between status polls

# ── Tool definitions (returned to Claude Desktop) ─────────────────────────────

TOOLS = [
    {
        "name": "request_authorization",
        "description": (
            "Submit an authorization request to Mighty before taking a consequential action. "
            "Call this BEFORE sending emails, making purchases, modifying files, posting content, "
            "or doing anything the user might want to review first. "
            "Returns a JSON object with 'request_id' and 'approval_url'. "
            "After calling this tool, share the approval_url with the user and ask them to approve or deny. "
            "Then call check_authorization with the request_id to get the final decision."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "description": "Short category, e.g. 'email', 'purchase', 'file', 'post'",
                },
                "label": {
                    "type": "string",
                    "description": "Human-readable description of exactly what you will do. Be specific.",
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "string"}},
                    "description": "Optional [['Key', 'Value']] detail pairs, e.g. [['To', 'alice@example.com']]",
                },
            },
            "required": ["action_type", "label"],
        },
    },
    {
        "name": "check_authorization",
        "description": (
            "Check whether the user has approved or denied an authorization request. "
            "Call this after request_authorization, once you have shared the approval_url with the user. "
            "Returns 'approved', 'denied', 'pending', or 'timeout'. "
            "If 'pending', wait a few seconds and call again. "
            "If 'approved', proceed with the action. If 'denied' or 'timeout', stop."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "request_id": {
                    "type": "string",
                    "description": "The request_id returned by request_authorization.",
                },
            },
            "required": ["request_id"],
        },
    },
    {
        "name": "record_action",
        "description": (
            "Log a completed action to the user's Mighty dashboard. "
            "Call this AFTER routine actions that don't need pre-approval "
            "but that the user would want a record of."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action_type": {"type": "string"},
                "label":       {"type": "string"},
                "outcome":     {"type": "string", "description": "e.g. 'completed', 'failed', 'skipped'"},
                "fields": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "string"}},
                },
            },
            "required": ["action_type", "label"],
        },
    },
]

# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _post(path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    req  = urllib.request.Request(
        BASE_URL + path,
        data=data,
        headers={"Content-Type": "application/json", "X-Mighty-Key": API_KEY},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _get(path: str) -> dict:
    req = urllib.request.Request(
        BASE_URL + path,
        headers={"X-Mighty-Key": API_KEY},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())

# ── Tool handlers ─────────────────────────────────────────────────────────────

def handle_request_authorization(args: dict) -> str:
    payload = {
        "api_key":     API_KEY,
        "action_type": args["action_type"],
        "label":       args["label"],
        "fields":      args.get("fields", []),
    }
    try:
        data = _post("/api/authorize", payload)
    except Exception as exc:
        return f"error: could not reach Mighty — {exc}"

    if data.get("status") != "pending":
        return f"error: unexpected response — {data}"

    return json.dumps({
        "request_id":   data["request_id"],
        "approval_url": data["approval_url"],
        "message":      "Share this approval_url with the user, then call check_authorization with the request_id.",
    })


def handle_check_authorization(args: dict) -> str:
    request_id = args["request_id"]
    try:
        status_data = _get(f"/api/status/{request_id}")
        return status_data.get("status", "pending")
    except Exception as exc:
        return f"error: could not reach Mighty — {exc}"


def handle_record_action(args: dict) -> str:
    payload = {
        "api_key":     API_KEY,
        "action_type": args["action_type"],
        "label":       args["label"],
        "outcome":     args.get("outcome", "completed"),
        "fields":      args.get("fields", []),
    }
    try:
        data = _post("/api/record", payload)
        return data.get("status", "logged")
    except Exception as exc:
        return f"error: could not reach Mighty — {exc}"

# ── MCP JSON-RPC loop ─────────────────────────────────────────────────────────

def send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> None:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue

        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue

        method = msg.get("method", "")
        msg_id = msg.get("id")

        # ── Handshake ──────────────────────────────────────────────────────
        if method == "initialize":
            send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities":    {"tools": {}},
                    "serverInfo":      {"name": "mighty", "version": "1.0.0"},
                },
            })

        elif method == "notifications/initialized":
            pass  # notification — no response

        # ── Tool discovery ─────────────────────────────────────────────────
        elif method == "tools/list":
            send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": TOOLS},
            })

        # ── Tool execution ─────────────────────────────────────────────────
        elif method == "tools/call":
            params    = msg.get("params", {})
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})

            try:
                if tool_name == "request_authorization":
                    result = handle_request_authorization(tool_args)
                elif tool_name == "check_authorization":
                    result = handle_check_authorization(tool_args)
                elif tool_name == "record_action":
                    result = handle_record_action(tool_args)
                else:
                    result = f"unknown tool: {tool_name}"

                send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": result}],
                    },
                })
            except Exception as exc:
                send({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": f"error: {exc}"}],
                        "isError": True,
                    },
                })

        # ── Unknown method — return a JSON-RPC error ───────────────────────
        elif msg_id is not None:
            send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            })


if __name__ == "__main__":
    main()
