"""Tests for versioned prompt loading."""

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.prompts import (
    PromptLoadError,
    get_prompt,
    list_prompts,
    render_prompt,
)


class TestPromptLoader:
    def test_list_prompts_includes_field_discovery(self):
        ids = {p.prompt_id for p in list_prompts()}
        assert "field_discovery" in ids
        assert "mighty_authorization" in ids

    def test_get_prompt_has_version(self):
        prompt = get_prompt("field_discovery")
        assert prompt.version == "1.0.0"
        assert prompt.version_label == "field_discovery@1.0.0"

    def test_render_field_discovery(self):
        rendered = render_prompt(
            "field_discovery",
            site="Delta Air Lines",
            text="Balance 45,320 miles",
            today="July 2, 2026",
            category_hint="",
        )
        assert rendered.version == "1.0.0"
        assert "Delta Air Lines" in rendered.text
        assert "Balance 45,320 miles" in rendered.text

    def test_render_missing_variable_raises(self):
        with pytest.raises(PromptLoadError, match="missing variables"):
            render_prompt("field_discovery", site="Amex")

    def test_render_mighty_authorization(self):
        rendered = render_prompt("mighty_authorization", api_key="test-key-123")
        assert "test-key-123" in rendered.text
        assert "MIGHTY AUTHORIZATION" in rendered.text

    def test_missing_prompt_raises(self):
        with pytest.raises(PromptLoadError, match="Prompt not found"):
            get_prompt("does_not_exist")
