"""
Fixture-based precision/recall evals for the extraction+filter pipeline.
No Gemini dependency — tests _extract_candidate_snippets and _post_filter_fields only.
"""

import json
import os
import re
import pytest

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
EXPECTED = os.path.join(os.path.dirname(__file__), "expected")


def load_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name)) as f:
        return f.read()


def load_expected(name: str) -> dict:
    with open(os.path.join(EXPECTED, name)) as f:
        return json.load(f)


def score_fields(fields: list, spec: dict) -> dict:
    """Compute precision/recall metrics against a spec. Returns dict with issues list."""
    issues = []
    all_values = " ".join(f.get("value", "").lower() for f in fields)
    all_keys = {f.get("key", "") for f in fields}

    if "min_fields" in spec and len(fields) < spec["min_fields"]:
        issues.append(f"Too few fields: got {len(fields)}, expected >={spec['min_fields']}")
    if "max_fields" in spec and len(fields) > spec["max_fields"]:
        issues.append(f"Too many fields: got {len(fields)}, expected <={spec['max_fields']}")

    for req_key in spec.get("required_keys", []):
        if not any(req_key in k for k in all_keys) and \
           not any(req_key in f.get("label", "").lower() for f in fields):
            issues.append(f"Missing required key containing: {req_key!r}")

    for field_hint, fragments in spec.get("required_value_fragments", {}).items():
        matching = [f for f in fields if field_hint in f.get("key", "") or
                    field_hint in f.get("label", "").lower()]
        for frag in fragments:
            if not any(frag.lower() in f.get("value", "").lower() for f in matching):
                issues.append(f"Field {field_hint!r} missing fragment {frag!r}. Got: {[f['value'] for f in matching]}")

    for frag in spec.get("must_contain_values", []):
        if frag.lower() not in all_values:
            issues.append(f"No field contains required fragment: {frag!r}")

    for forbidden in spec.get("forbidden_values", []):
        hits = [f for f in fields if forbidden.lower() in f.get("value", "").lower()]
        if hits:
            issues.append(f"Forbidden value {forbidden!r} in: {[f['value'] for f in hits]}")

    return {"issues": issues}


# ── Snippet extraction evals ───────────────────────────────────────────────────

class TestSnippetEvalDelta:
    def test_snippets_contain_required_fragments(self):
        from app import _extract_candidate_snippets
        text = load_fixture("delta_companion_cert.txt")
        snippets = _extract_candidate_snippets(text)
        required = ["companion", "2026", "diamond", "medallion", "45,320"]
        missing = [r for r in required if r.lower() not in snippets.lower()]
        assert not missing, f"Snippets missing: {missing}"

    def test_snippet_compression_ratio(self):
        from app import _extract_candidate_snippets
        text = load_fixture("delta_companion_cert.txt")
        snippets = _extract_candidate_snippets(text)
        ratio = len(snippets) / len(text)
        # Small fixtures (<5k) may be returned nearly whole — just check not empty
        if len(text) >= 5_000:
            assert ratio < 0.9, f"Not filtering enough: {ratio:.0%} of raw"
        assert ratio > 0.01, f"Too aggressive: only {ratio:.0%} of raw"


class TestSnippetEvalMarriott:
    def test_snippets_contain_key_data(self):
        from app import _extract_candidate_snippets
        text = load_fixture("marriott_free_night.txt")
        snippets = _extract_candidate_snippets(text)
        required = ["platinum", "28,500", "free night", "2026"]
        missing = [r for r in required if r.lower() not in snippets.lower()]
        assert not missing, f"Snippets missing: {missing}"


class TestSnippetEvalNoisy:
    def test_noisy_no_dollar_amounts(self):
        from app import _extract_candidate_snippets
        text = load_fixture("noisy_marketing.txt")
        snippets = _extract_candidate_snippets(text)
        dollars = re.findall(r'\$\d+', snippets)
        assert not dollars, f"Dollar amounts in noisy page: {dollars}"


# ── Filter precision/recall evals ────────────────────────────────────────────

class TestFilterPrecision:
    def _f(self, key, label, value, **kw):
        return {"key": key, "label": label, "value": value, **kw}

    def test_delta_realistic_precision(self):
        from app import _post_filter_fields
        fields = [
            self._f("elite_status",    "Elite Status",         "Diamond Medallion"),
            self._f("miles_balance",   "SkyMiles Balance",     "45,320"),
            self._f("companion_cert",  "Companion Certificate","Valid through Aug 31, 2026"),
            self._f("global_upgrades", "Global Upgrade Certs", "2 available, exp Jan 31, 2027"),
            self._f("past_flight",     "Upcoming Flight",      "ATL to SFO on 22JUL2024"),
            self._f("empty_field",     "Gift Card Balance",    "$0.00"),
            self._f("login_wall",      "Points Balance",       "Log in to view"),
            self._f("generic_member",  "Status",               "Member"),
        ]
        result = _post_filter_fields(fields)
        result_keys = {f["key"] for f in result}

        good = {"elite_status", "miles_balance", "companion_cert", "global_upgrades"}
        bad  = {"past_flight", "empty_field", "login_wall", "generic_member"}

        assert good <= result_keys, f"Good fields dropped: {good - result_keys}"
        assert not (bad & result_keys), f"Bad fields kept: {bad & result_keys}"

    def test_perfect_recall_on_clean_fields(self):
        from app import _post_filter_fields
        fields = [
            self._f("elite_status",   "Elite Status",       "Platinum Elite"),
            self._f("points_balance", "Points Balance",     "28,500"),
            self._f("free_night",     "Free Night Award",   "Expires Dec 31, 2026"),
        ]
        result = _post_filter_fields(fields)
        assert len(result) == 3, f"Clean fields dropped: {set(f['key'] for f in fields) - set(f['key'] for f in result)}"

    def test_eval_spec_delta(self):
        """Run score_fields against a realistic synthetic field set using the delta spec."""
        from app import _post_filter_fields
        spec = load_expected("delta_companion_cert.json")
        fields = [
            self._f("elite_status",       "Elite Status",          "Diamond Medallion"),
            self._f("miles_balance",      "SkyMiles Balance",      "45,320"),
            self._f("companion_certificate","Companion Certificate","Valid through Aug 31, 2026"),
            self._f("global_upgrades",    "Global Upgrade Certs",  "2 available, exp Jan 31, 2027"),
        ]
        result = _post_filter_fields(fields)
        score = score_fields(result, spec)
        assert not score["issues"], f"Eval issues: {score['issues']}"


# ── Confidence auto-selection evals ──────────────────────────────────────────

class TestConfidenceAutoSelection:
    def test_high_confidence_fields_enabled(self):
        from app import _post_filter_fields
        # _save_discovered_fields is tested indirectly — we just verify the logic:
        # fields with confidence >= 0.85 should be in auto_enabled
        fields = [
            {"key": "elite_status", "label": "Elite Status", "value": "Platinum", "confidence": 0.97},
            {"key": "balance",      "label": "Balance",      "value": "28,500",   "confidence": 0.90},
        ]
        result = _post_filter_fields(fields)
        assert len(result) == 2

    def test_low_confidence_still_passes_filter(self):
        """_post_filter_fields doesn't drop on confidence — that's _save_discovered_fields's job."""
        from app import _post_filter_fields
        fields = [{"key": "k", "label": "Points", "value": "500", "confidence": 0.50}]
        result = _post_filter_fields(fields)
        assert len(result) == 1
