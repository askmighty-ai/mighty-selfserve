#!/usr/bin/env python3
"""One-shot installer for the production AI platform layer."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def patch_app_py() -> None:
    path = ROOT / "app.py"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line.startswith("DISCOVER_PROMPT"))
    end = next(i for i in range(start, len(lines)) if lines[i].strip().endswith('"""'))
    text = "".join(lines[:start] + lines[end + 1 :])

    text = text.replace(
        "  AI_FIELD_DISCOVERY_FAILURE_TTL_SECONDS — Negative-cache TTL after failures (default: 300)\n\"\"\"",
        "  AI_FIELD_DISCOVERY_FAILURE_TTL_SECONDS — Negative-cache TTL after failures (default: 300)\n"
        "  AI_REQUEST_TIMEOUT_SECONDS — Provider request timeout (default: 60)\n"
        "  AI_REQUEST_MAX_RETRIES — Retries for transient provider errors (default: 3)\n"
        "  AI_REQUEST_RETRY_BACKOFF_SECONDS — Initial retry backoff (default: 1.0)\n\"\"\"",
    )
    text = text.replace(
        "    truncate_discovery_input,\n)\n\n\ndef claude_discover_fields",
        "    truncate_discovery_input,\n)\nfrom mighty.ai_platform import (\n"
        "    build_field_discovery_context,\n    discovery_log_suffix,\n"
        "    record_field_discovery_cache_hit,\n    render_field_discovery_prompt_text,\n"
        "    render_missing_pages_prompt,\n)\n\n\ndef claude_discover_fields",
    )
    text = text.replace(
        "    if cached_fields is not None:\n        return cached_fields",
        "    if cached_fields is not None:\n        record_field_discovery_cache_hit(site_name=site_name)\n        return cached_fields",
    )
    text = re.sub(
        r"        prompt = DISCOVER_PROMPT\.format\(\n            site=site_name,\n            text=snippets,\n            today=_today_str,\n            category_hint=category_hint,\n        \)\n        context = DiscoveryContext\(\n            site_name=site_name,\n            source=source,\n            prompt=prompt,\n            today=_today_str,\n            category_hint=category_hint,\n        \)",
        "        context = build_field_discovery_context(\n            site_name=site_name,\n            source=source,\n            snippets=snippets,\n            today=_today_str,\n            category_hint=category_hint,\n        )",
        text,
        count=1,
    )
    text = re.sub(
        r"        prompt = DISCOVER_PROMPT\.format\(\n            site=site_name, text=snippets,\n            today=_today_str, category_hint=category_hint\n        \)",
        "        prompt = render_field_discovery_prompt_text(\n            site_name=site_name, snippets=snippets,\n            today=_today_str, category_hint=category_hint,\n        )",
        text,
        count=1,
    )
    text = text.replace(
        'f"{len(result.fields)} raw fields",\n            flush=True,',
        'f"{len(result.fields)} raw fields{discovery_log_suffix(result)}",\n            flush=True,',
    )
    text = text.replace(
        '                    page_prompt = (\n                        f"Based on this {source} account page text, what specific page URLs or sections "\n                        f"are probably missing that would contain: {missing_str}?\\n\\n"\n                        "List only specific paths like /my-account/certificates or /loyalty/wallet. "\n                        "Max 5 paths. One per line. No explanation."\n                    )',
        "                    page_prompt = render_missing_pages_prompt(source=source, missing_str=missing_str)",
    )
    text = re.sub(
        r"def build_prompt\(api_key, url\):\n    return \(\n.*?\n    \)\n",
        'def build_prompt(api_key, url):\n    _ = url\n    from mighty.prompts import render_prompt\n    return render_prompt("mighty_authorization", api_key=api_key).text\n',
        text,
        count=1,
        flags=re.DOTALL,
    )
    text = re.sub(
        r'def call_claude_for_prompt\(description, api_key, url\):\n'
        r'    """Call Claude Haiku to generate a tailored checkpoint prompt from an agent description\."""\n'
        r"    system = \(\n.*?\n    \)\n    user_msg = \(\n.*?\n    \)\n",
        'def call_claude_for_prompt(description, api_key, url):\n'
        '    """Call Claude Haiku to generate a tailored checkpoint prompt from an agent description."""\n'
        '    from mighty.prompts import render_prompt\n'
        '    system = render_prompt("onboarding_checkpoint_system").text\n'
        '    user_msg = render_prompt("onboarding_checkpoint_user", description=description, api_key=api_key, url=url).text\n',
        text,
        count=1,
        flags=re.DOTALL,
    )
    if "DISCOVER_PROMPT" in text:
        raise RuntimeError("DISCOVER_PROMPT still present after patch")
    path.write_text(text, encoding="utf-8")


def extract_field_discovery_prompt() -> None:
    app = (ROOT / "app.py").read_text(encoding="utf-8")
    match = re.search(r'DISCOVER_PROMPT = """(.*?)"""', app, re.DOTALL)
    if not match:
        return
    front = """---
id: field_discovery
version: "1.0.0"
description: Extract personalized account fields from scraped page text
variables:
  - site
  - text
  - today
  - category_hint
---

"""
    out = ROOT / "prompts" / "field_discovery.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(front + match.group(1), encoding="utf-8")


if __name__ == "__main__":
    extract_field_discovery_prompt()
    patch_app_py()
    print("AI platform installed")
