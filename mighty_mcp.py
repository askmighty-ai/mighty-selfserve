#!/usr/bin/env python3
"""
Mighty MCP Server
=================
Gives Claude Desktop an independent approval and activity log for consequential actions.

How it works
------------
1. Before a consequential action (email, purchase, file deletion, etc.), Claude calls
   request_authorization with the FULL content of what it's about to do.
2. Mighty creates a pending record and returns an approval_url.
3. Claude shares the approval_url with the user. The user opens it in their browser,
   sees exactly what was submitted, and clicks Approve or Deny — inside Mighty.
4. Claude calls check_authorization to poll for the decision.
5. Approved: Claude proceeds. Denied: Claude stops.

Because the user approves inside Mighty's interface (not in the chat), Mighty's log
is a verified record of what the user actually saw and agreed to.

Setup — add to ~/Library/Application Support/Claude/claude_desktop_config.json:
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

Environment variables
---------------------
MIGHTY_API_KEY   — required. Your Mighty API key (from Settings).
MIGHTY_BASE_URL  — optional. Defaults to the hosted Mighty instance.
"""

import json
import os
import sys
import urllib.error
import urllib.request

# ── Config ────────────────────────────────────────────────────────────────────

API_KEY  = os.environ.get("MIGHTY_API_KEY", "")
BASE_URL = os.environ.get("MIGHTY_BASE_URL", "https://mighty-selfserve-production.up.railway.app").rstrip("/")

if not API_KEY:
    sys.stderr.write("[mighty] WARNING: MIGHTY_API_KEY is not set. All requests will fail.\n")
    sys.stderr.flush()

# ── Tool definitions ──────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "request_authorization",
        "description": (
            "Submit an authorization request to Mighty BEFORE taking any consequential action — "
            "sending emails, making purchases, modifying or deleting files, posting content, "
            "submitting forms, making API calls that have real-world effects. "
            "\n\n"
            "IMPORTANT: Include the FULL content in fields — not a summary. "
            "For email: To, Subject, and the complete body. "
            "For purchases: merchant, item, and exact amount. "
            "For file operations: the file path and what will change. "
            "The fields you submit become the permanent record of what was approved. "
            "They must contain enough detail to verify exactly what happened. "
            "\n\n"
            "This tool returns a request_id and an approval_url. "
            "Tell the user: 'Please approve this action in Mighty: <approval_url>'. "
            "Then call check_authorization with the request_id to wait for their decision."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "description": "Short category: 'email', 'purchase', 'file_edit', 'deletion', 'api_call', 'post', etc.",
                },
                "label": {
                    "type": "string",
                    "description": "Plain-English description of the specific action. Be specific: 'Send email to john@example.com re: project update' not 'Send email'.",
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "string", "minItems": 2, "maxItems": 2}},
                    "description": (
                        "Full content as [['Key', 'Value']] pairs. Include everything the user would need "
                        "to verify exactly what was approved. "
                        "Email example: [['To', 'alice@example.com'], ['Subject', 'Q3 update'], ['Body', '<full text>']]. "
                        "Purchase example: [['Merchant', 'AWS'], ['Item', 'Reserved Instance m5.xlarge'], ['Amount', '$4,200.00']]."
                    ),
                },
                "consequence_level": {
                    "type": "string",
                    "enum": ["routine", "sensitive", "critical"],
                    "description": "How consequential is this action? routine = easily reversed, sensitive = hard to reverse, critical = financial/legal/irreversible.",
                },
            },
            "required": ["action_type", "label", "fields"],
        },
    },
    {
        "name": "check_authorization",
        "description": (
            "Check whether the user has approved or denied an authorization request in Mighty. "
            "Call this after request_authorization, once you have shared the approval_url with the user. "
            "Returns 'approved', 'denied', 'pending', or 'timeout'. "
            "If 'pending', wait a few seconds and call again — the user may still be reviewing. "
            "If 'approved', proceed with the action. "
            "If 'denied' or 'timeout', stop and tell the user."
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
            "Log a completed action to the user's Mighty activity log. "
            "Use this for routine actions that don't require pre-approval but that the user "
            "would want a record of — reading files, running searches, fetching data. "
            "Include the full content in fields, same as request_authorization."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "description": "Short category: 'file_read', 'search', 'fetch', etc.",
                },
                "label": {
                    "type": "string",
                    "description": "Plain-English description of what was done.",
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "string"}},
                    "description": "Full content as [['Key', 'Value']] pairs.",
                },
                "outcome": {
                    "type": "string",
                    "description": "'completed', 'failed', or 'skipped'.",
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
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body}") from e


def _get(path: str) -> dict:
    req = urllib.request.Request(
        BASE_URL + path,
        headers={"X-Mighty-Key": API_KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body}") from e

# ── Tool handlers ─────────────────────────────────────────────────────────────

def handle_request_authorization(args: dict) -> str:
    payload = {
        "api_key":           API_KEY,
        "action_type":       args["action_type"],
        "label":             args["label"],
        "fields":            args.get("fields", []),
        "consequence_level": args.get("consequence_level", "routine"),
    }
    try:
        data = _post("/api/authorize", payload)
    except Exception as exc:
        return f"error: could not reach Mighty — {exc}"

    if data.get("status") != "pending":
        return f"error: unexpected response — {data}"

    approval_url = data.get("approval_url", "")
    request_id   = data.get("request_id", "")

    return json.dumps({
        "request_id":   request_id,
        "approval_url": approval_url,
        "instructions": (
            f"Tell the user: 'Please approve this action in Mighty: {approval_url}' "
            f"Then call check_authorization with request_id='{request_id}' to wait for their decision."
        ),
    })


def handle_check_authorization(args: dict) -> str:
    request_id = args["request_id"]
    try:
        data = _get(f"/api/status/{request_id}")
        status = data.get("status", "pending")
        if status == "approved":
            return "approved — proceed with the action"
        elif status == "denied":
            return "denied — stop and tell the user their request was denied in Mighty"
        elif status in ("timeout", "expired"):
            return "timeout — the request expired without a decision; stop and ask the user what they want to do"
        else:
            return "pending — the user has not yet decided; wait a few seconds and call again"
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
        return f"logged — record_id: {data.get('record_id', '?')}"
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

        if method == "initialize":
            send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities":    {"tools": {}},
                    "serverInfo":      {"name": "mighty", "version": "1.1.0"},
                },
            })

        elif method == "notifications/initialized":
            pass  # notification — no response needed

        elif method == "tools/list":
            send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": TOOLS},
            })

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
                    "result": {"content": [{"type": "text", "text": result}]},
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

        elif msg_id is not None:
            send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            })


if __name__ == "__main__":
    main()
