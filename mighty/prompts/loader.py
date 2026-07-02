"""
Load versioned prompts from prompts/*.md files.

Each file uses YAML frontmatter for metadata and a markdown body as the template.
Templates use Python str.format placeholders, e.g. {site} and {text}.
Literal braces in the body must be escaped as {{ and }}.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


class PromptLoadError(Exception):
    """Raised when a prompt file is missing or malformed."""


@dataclass(frozen=True)
class PromptDefinition:
    prompt_id: str
    version: str
    description: str
    body: str
    variables: tuple[str, ...] = ()
    path: str = ""

    @property
    def version_label(self) -> str:
        return f"{self.prompt_id}@{self.version}"


@dataclass(frozen=True)
class RenderedPrompt:
    prompt_id: str
    version: str
    text: str

    @property
    def version_label(self) -> str:
        return f"{self.prompt_id}@{self.version}"


def _prompts_dir() -> Path:
    return _PROMPTS_DIR


def _parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    match = _FRONTMATTER_RE.match(raw)
    if not match:
        raise PromptLoadError("Prompt file must start with YAML frontmatter (---)")

    meta_block = match.group(1)
    body = raw[match.end() :]

    meta: dict[str, Any] = {}
    current_key: str | None = None
    list_values: list[str] = []
    in_list = False

    for line in meta_block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- ") and in_list and current_key:
            list_values.append(
                stripped[2:].strip().strip('"').strip("'")
            )
            continue
        if ":" not in line:
            continue
        if in_list and current_key:
            meta[current_key] = tuple(list_values)
            list_values = []
            in_list = False

        key, _, value = line.partition(":")
        current_key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not value:
            in_list = True
            list_values = []
        else:
            meta[current_key] = value
            in_list = False

    if in_list and current_key:
        meta[current_key] = tuple(list_values)

    return meta, body


def _load_prompt_file(path: Path) -> PromptDefinition:
    raw = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(raw)

    prompt_id = str(meta.get("id") or path.stem)
    version = str(meta.get("version") or "0.0.0")
    description = str(meta.get("description") or "")

    variables_raw = meta.get("variables", ())
    if isinstance(variables_raw, str):
        variables = (variables_raw,)
    else:
        variables = tuple(str(v) for v in variables_raw)

    return PromptDefinition(
        prompt_id=prompt_id,
        version=version,
        description=description,
        body=body,
        variables=variables,
        path=str(path),
    )


@lru_cache(maxsize=32)
def get_prompt(prompt_id: str) -> PromptDefinition:
    """Load a prompt definition by id (filename without .md)."""
    path = _prompts_dir() / f"{prompt_id}.md"
    if not path.is_file():
        raise PromptLoadError(f"Prompt not found: {prompt_id} ({path})")
    return _load_prompt_file(path)


def render_prompt(prompt_id: str, **variables: Any) -> RenderedPrompt:
    """Load and render a prompt template with the given variables."""
    definition = get_prompt(prompt_id)
    missing = [name for name in definition.variables if name not in variables]
    if missing:
        raise PromptLoadError(
            f"Prompt {definition.version_label} missing variables: {', '.join(missing)}"
        )
    try:
        text = definition.body.format(**variables)
    except KeyError as exc:
        raise PromptLoadError(
            f"Prompt {definition.version_label} template error: {exc}"
        ) from exc

    return RenderedPrompt(
        prompt_id=definition.prompt_id,
        version=definition.version,
        text=text,
    )


def list_prompts() -> list[PromptDefinition]:
    """Return all prompt definitions in prompts/."""
    directory = _prompts_dir()
    if not directory.is_dir():
        return []
    return [_load_prompt_file(path) for path in sorted(directory.glob("*.md"))]


def clear_prompt_cache() -> None:
    get_prompt.cache_clear()
