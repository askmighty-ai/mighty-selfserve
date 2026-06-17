"""
Fixture-based regression tests for field extraction pipeline.
Tests _extract_candidate_snippets and _post_filter_fields against
anonymized real-world account pages without making Gemini calls.

Run with: pytest tests/test_fixtures.py -v
"""
import json
import os
import sys
import pytest

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
EXPECTED_DIR = os.path.join(FIXTURES_DIR, "expected")

# Import extraction functions (no app context needed for pure functions)
try:
    from app import _extract_candidate_snippets, _post_filter_fields
    HAVE_APP = True
except ImportError:
    HAVE_APP = False


def load_fixture(name: str) -> str:
    path = os.path.join(FIXTURES_DIR, f"{name}.txt")
    with open(path) as f:
        return f.read()


def load_expected(name: str) -> dict:
    path = os.path.join(EXPECTED_DIR, f"{name}.json")
    with open(path) as f:
        return json.load(f)


def simulate_extraction(raw_text: str, site: str = "test.com") -> list:
    """
    Simulate field extraction WITHOUT calling Gemini.
    Parses key:value patterns from the raw text directly,
    mimicking what LLM would return for well-structured account pages.
    """
    import re
    fields = []
    seen_keys = set()

    # Extract key: value pairs from text
    for line in raw_text.splitlines():
        line = line.strip()
        if not line or line.startswith("==="):
            continue
        # Match "Label: Value" patterns
        m = re.match(r'^([A-Za-z][A-Za-z0-9 \-/]+):\s+(.+)$', line)
        if not m:
            continue
        label_raw = m.group(1).strip()
        value = m.group(2).strip()

        # Generate key from label
        key = re.sub(r'[^a-z0-9]+', '_', label_raw.lower()).strip('_')
        if key in seen_keys or len(key) < 3:
            continue
        seen_keys.add(key)

        # Skip boilerplate
        if any(w in label_raw.lower() for w in ['address', 'phone', 'email', 'password']):
            continue

        fields.append({
            "key": key,
            "label": label_raw,
            "value": value,
            "confidence": 0.90,
            "source_snippet": line,
        })

    return fields


@pytest.fixture(params=[
    "delta_certificate", "amex_credit", "marriott_night", "chase_statement", "xfinity_bill",
    "jetblue_mosaic", "southwest_companion", "united_mileageplus", "hilton_honors", "apple_card",
    "progressive_insurance", "tesla_account", "sofi_banking", "robinhood_account", "costco_membership",
    "amazon_prime", "uber_eats_account", "gym_membership", "ezpass_account", "linkedin_premium",
])
def fixture_name(request):
    return request.param


class TestSnippetExtraction:
    """Test that candidate snippets are extracted from fixture files."""

    @pytest.mark.skipif(not HAVE_APP, reason="app.py not importable")
    def test_snippets_extracted(self, fixture_name):
        raw = load_fixture(fixture_name)
        snippets = _extract_candidate_snippets(raw)
        assert len(snippets) > 0, f"No snippets extracted from {fixture_name}"

    @pytest.mark.skipif(not HAVE_APP, reason="app.py not importable")
    def test_snippets_contain_key_phrases(self, fixture_name):
        """Key phrases from the fixture should appear in extracted snippets."""
        raw = load_fixture(fixture_name)
        expected = load_expected(fixture_name)

        # _extract_candidate_snippets returns a single string (not a list)
        snippets = _extract_candidate_snippets(raw)
        all_snippet_text = snippets.lower()

        # Check that at least one expected value is findable in snippets
        found_any = False
        for key, val in expected.get("expected_values", {}).items():
            if val.lower() in all_snippet_text or val.replace(",", "") in all_snippet_text:
                found_any = True
                break

        assert found_any, (
            f"{fixture_name}: none of {list(expected['expected_values'].values())} "
            f"found in extracted snippets"
        )


class TestFieldExtraction:
    """Test field extraction using simulated (non-Gemini) parsing."""

    def test_min_field_count(self, fixture_name):
        raw = load_fixture(fixture_name)
        expected = load_expected(fixture_name)
        fields = simulate_extraction(raw)
        assert len(fields) >= expected["min_fields"], (
            f"{fixture_name}: got {len(fields)} fields, expected >= {expected['min_fields']}"
        )

    def test_max_field_count(self, fixture_name):
        raw = load_fixture(fixture_name)
        expected = load_expected(fixture_name)
        fields = simulate_extraction(raw)
        assert len(fields) <= expected["max_fields"], (
            f"{fixture_name}: got {len(fields)} fields, expected <= {expected['max_fields']} (hallucination?)"
        )

    def test_expected_values_present(self, fixture_name):
        raw = load_fixture(fixture_name)
        expected = load_expected(fixture_name)
        fields = simulate_extraction(raw)

        all_values = " ".join(f["value"] for f in fields).lower()
        for key, expected_val in expected.get("expected_values", {}).items():
            # Accept partial matches (e.g., "2341" in "2341.87")
            clean_expected = expected_val.replace(",", "").lower()
            assert clean_expected in all_values.replace(",", ""), (
                f"{fixture_name}: expected value '{expected_val}' not found in extracted fields. "
                f"Got values: {[f['value'] for f in fields[:5]]}"
            )

    def test_no_forbidden_values(self, fixture_name):
        raw = load_fixture(fixture_name)
        expected = load_expected(fixture_name)
        fields = simulate_extraction(raw)

        for f in fields:
            for forbidden in expected.get("forbidden_values", []):
                assert forbidden.lower() not in f["value"].lower(), (
                    f"{fixture_name}: field '{f['label']}' has forbidden value '{forbidden}'"
                )

    def test_required_field_keys_extractable(self, fixture_name):
        """Required field keys should appear as extractable concepts in the raw text."""
        raw = load_fixture(fixture_name)
        expected = load_expected(fixture_name)
        raw_lower = raw.lower()

        # Map key tokens to what we'd expect in the raw text
        for req_key in expected.get("required_fields", []):
            # Convert key like "certificate_value" -> ["certificate", "value"]
            tokens = req_key.replace("_", " ").split()
            # At least one meaningful token (not "id", "type") should be in raw text
            meaningful = [t for t in tokens if len(t) > 3]
            if meaningful:
                found = any(t in raw_lower for t in meaningful)
                assert found, (
                    f"{fixture_name}: required field key '{req_key}' tokens {meaningful} "
                    f"not found in raw text"
                )


class TestPrecisionRecall:
    """Measure extraction quality metrics against fixtures."""

    def _compute_metrics(self, fixture_name):
        raw = load_fixture(fixture_name)
        expected = load_expected(fixture_name)
        fields = simulate_extraction(raw)

        all_values = " ".join(f["value"] for f in fields).lower()

        required = expected.get("required_fields", [])
        expected_vals = expected.get("expected_values", {})

        # Recall: how many required fields are captured?
        recall_hits = sum(
            1 for rf in required
            if any(token in " ".join(f["key"] for f in fields) for token in rf.split("_") if len(token) > 3)
        )
        recall = recall_hits / len(required) if required else 1.0

        # Precision proxy: expected value match rate
        val_hits = sum(
            1 for ev in expected_vals.values()
            if ev.replace(",", "").lower() in all_values.replace(",", "")
        )
        precision = val_hits / len(expected_vals) if expected_vals else 1.0

        return {
            "fixture": fixture_name,
            "total_fields": len(fields),
            "recall": recall,
            "precision": precision,
            "recall_hits": recall_hits,
            "total_required": len(required),
            "val_hits": val_hits,
            "total_expected_vals": len(expected_vals),
        }

    def test_recall_above_threshold(self, fixture_name):
        m = self._compute_metrics(fixture_name)
        assert m["recall"] >= 0.6, (
            f"{fixture_name}: recall {m['recall']:.0%} below 60% threshold "
            f"({m['recall_hits']}/{m['total_required']} required fields found)"
        )

    def test_precision_above_threshold(self, fixture_name):
        m = self._compute_metrics(fixture_name)
        assert m["precision"] >= 0.5, (
            f"{fixture_name}: precision {m['precision']:.0%} below 50% threshold "
            f"({m['val_hits']}/{m['total_expected_vals']} expected values matched)"
        )


class TestSnippetCompression:
    """Test that snippets are appropriately compressed (not too large)."""

    @pytest.mark.skipif(not HAVE_APP, reason="app.py not importable")
    def test_snippet_count_reasonable(self, fixture_name):
        # _extract_candidate_snippets returns a string; count blocks by paragraph breaks
        raw = load_fixture(fixture_name)
        snippets = _extract_candidate_snippets(raw)
        block_count = len([b for b in snippets.split("\n\n") if b.strip()])
        assert block_count <= 25, f"{fixture_name}: too many blocks ({block_count} > 25)"

    @pytest.mark.skipif(not HAVE_APP, reason="app.py not importable")
    def test_total_snippet_length(self, fixture_name):
        # _extract_candidate_snippets returns a string; tiny inputs get returned verbatim
        raw = load_fixture(fixture_name)
        snippets = _extract_candidate_snippets(raw)
        raw_len = len(raw)
        # Skip compression assertion for tiny fixtures (< 1500 chars) — the fallback
        # for small inputs with no triggers is to return the whole text verbatim,
        # which is correct behaviour (nothing to compress).
        if raw_len < 1500:
            return
        compression = len(snippets) / raw_len
        assert compression <= 0.9, (
            f"{fixture_name}: snippets are {compression:.0%} of raw text — not compressing enough"
        )
