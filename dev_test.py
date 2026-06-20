#!/usr/bin/env python3
"""
Mighty local dev test harness.
Uses Flask's test client — no running server needed.

Usage:
  python3 dev_test.py                   # run all tests
  python3 dev_test.py connector         # connector path tests only
  python3 dev_test.py intercept         # full intercept pipeline
  python3 dev_test.py discover hilton   # run discover-now for a source
  python3 dev_test.py health            # site health report

Set GEMINI_API_KEY env var to enable live Gemini tests.
"""

import os, sys, json, sqlite3, tempfile, time

# ── Bootstrap env ────────────────────────────────────────────────────────────
os.environ.setdefault("SECRET_KEY", "dev-test-secret-key-mighty-2026")
os.environ.setdefault("DATABASE_PATH", tempfile.mktemp(suffix=".db"))
os.environ.setdefault("PORT", "5099")

import app as mighty

# ── Helpers ──────────────────────────────────────────────────────────────────

def make_client():
    mighty.app.config["TESTING"] = True
    mighty.app.config["WTF_CSRF_ENABLED"] = False
    return mighty.app.test_client()


def register_and_login(client, email="dev@mighty.local", password="devpass123"):
    client.post("/register", data={"email": email, "password": password, "name": "Dev"})
    client.post("/login",    data={"email": email, "password": password})
    # Get API key from DB
    db = mighty.get_db()
    row = db.execute("SELECT api_key FROM users WHERE email=?", (email,)).fetchone()
    return row["api_key"] if row else None


def api(client, method, path, api_key=None, **kwargs):
    headers = kwargs.pop("headers", {})
    if api_key:
        headers["X-Mighty-Key"] = api_key
    fn = getattr(client, method.lower())
    resp = fn(path, headers=headers, **kwargs)
    try:
        return resp.status_code, resp.get_json()
    except Exception:
        return resp.status_code, resp.data.decode()


# ── Test suites ──────────────────────────────────────────────────────────────

def test_connectors():
    """Unit-test connector path extraction — no HTTP, no DB, no Gemini."""
    print("\n=== Connector path tests ===")
    cases = [
        # (source, json_payload, expected_key, expected_value_contains)
        ("hilton",    {"loyalty": {"tier": "Gold", "points": 143996}},
                      "elite_status", "Gold"),
        ("hilton",    {"loyalty": {"tier": "Gold", "points": 143996}},
                      "points_balance", "143996"),
        ("delta",     {"data": {"member": {"medallionStatus": "Platinum", "skymiles": 55000}}},
                      "elite_status", "Platinum"),
        ("delta",     {"data": {"member": {"medallionStatus": "Platinum", "skymiles": 55000}}},
                      "points_balance", "55000"),
        ("united",    {"mpAccount": {"eliteStatus": "Premier Gold", "balance": 32000}},
                      "elite_status", "Premier Gold"),
        ("marriott",  {"props": {"pageProps": {"memberProfile": {"tier": "Platinum", "points": 88000}}}},
                      "elite_status", "Platinum"),
        ("southwest", {"account": {"tierName": "A-List", "pointsBalance": 24617}},
                      "elite_status", "A-List"),
        # Negative: source with no connector → empty
        ("unknown_site", {"loyalty": {"tier": "Gold"}}, None, None),
        # Negative: empty values should not match
        ("hilton",    {"loyalty": {"tier": "", "points": 0}}, None, None),
    ]

    passed = failed = 0
    for source, payload, expected_key, expected_val in cases:
        fields = mighty.try_connector_paths(source, json.dumps(payload))
        field_map = {f["key"]: f["value"] for f in fields}

        if expected_key is None:
            # Expect no fields
            if fields:
                print(f"  FAIL  {source}: expected no fields, got {list(field_map.keys())}")
                failed += 1
            else:
                print(f"  PASS  {source}: correctly returned no fields")
                passed += 1
        else:
            if expected_key in field_map and expected_val in str(field_map[expected_key]):
                print(f"  PASS  {source}.{expected_key} = {field_map[expected_key]!r}")
                passed += 1
            else:
                got = field_map.get(expected_key, "MISSING")
                print(f"  FAIL  {source}.{expected_key}: expected {expected_val!r}, got {got!r}")
                failed += 1

    print(f"\n  {passed} passed, {failed} failed")
    return failed == 0


def test_intercept_pipeline():
    """End-to-end: register user → connect account → POST intercept → check fields saved."""
    print("\n=== Intercept pipeline test ===")
    with make_client() as client:
        with mighty.app.app_context():
            api_key = register_and_login(client)
            if not api_key:
                print("  FAIL  Could not register/login")
                return False
            print(f"  API key: {api_key[:12]}...")

            # Connect a hilton account manually in DB (skip full UI flow)
            uid = mighty.get_db().execute(
                "SELECT id FROM users WHERE api_key=?", (api_key,)
            ).fetchone()["id"]

            # Insert minimal account_data row for hilton
            mighty.get_db().execute(
                "INSERT OR REPLACE INTO account_data "
                "(user_id, source, display_name, icon, color, data_enc, synced_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (uid, "hilton", "Hilton Honors", "🏨", "#003087",
                 mighty.encrypt_account_data(uid, {"raw_text": "", "sync_status": "ok", "items": []}),
                 mighty.iso())
            )
            mighty.get_db().commit()

            # Also insert domain approval
            mighty.get_db().execute(
                "INSERT OR IGNORE INTO domain_approvals (user_id, domain, approved_at) VALUES (?,?,?)",
                (uid, "hilton.com", mighty.iso())
            )
            mighty.get_db().commit()

            # POST intercept with a Hilton-shaped payload
            hilton_payload = {"loyalty": {"tier": "Gold", "points": 143996}}
            status, body = api(
                client, "POST", "/api/extension/intercept",
                api_key=api_key,
                json={
                    "source":    "hilton",
                    "url":       "https://www.hilton.com/en/hilton-honors/guest/my-account/",
                    "json_data": json.dumps(hilton_payload),
                }
            )
            print(f"  Intercept response: {status} {body}")
            if status != 200:
                print("  FAIL  Intercept rejected")
                return False

            # Give background thread a moment
            time.sleep(1)

            # Check saved fields via /api/debug/fields
            status2, body2 = api(client, "GET", "/api/debug/fields/hilton")
            print(f"  Fields response: {status2}")
            if isinstance(body2, dict):
                items = body2.get("items", [])
                print(f"  Saved {len(items)} item(s):")
                for it in items:
                    connector_tag = " [connector]" if it.get("from_connector") else ""
                    print(f"    {it.get('key')}: {it.get('value')}{connector_tag}")
                elite = next((i for i in items if i.get("key") == "elite_status"), None)
                if elite and elite.get("value") == "Gold":
                    print("  PASS  elite_status = Gold saved correctly via connector")
                    return True
                else:
                    print("  FAIL  elite_status not found or wrong value")

    return False


def test_site_health():
    """Hit /api/admin/site-health and print the report."""
    print("\n=== Site health report ===")
    with make_client() as client:
        with mighty.app.app_context():
            register_and_login(client)
            status, body = api(client, "GET", "/api/admin/site-health")
            if status != 200:
                print(f"  FAIL  {status} {body}")
                return False
            sites = body.get("sites", [])
            print(f"  {'Source':<20} {'Rate':>6}  {'Connector':>10}  {'Fields'}")
            print(f"  {'-'*60}")
            for s in sites:
                connector = "✓" if s["connector_supported"] else "–"
                keys = ", ".join(s["field_keys"][:3]) or "(none)"
                print(f"  {s['source']:<20} {s['extraction_rate_pct']:>5}%  {connector:>10}  {keys}")
            return True


def test_candidate_logging():
    """Verify _record_connector_candidates logs paths from Gemini output."""
    print("\n=== Connector candidate logging test ===")
    import io, contextlib

    delta_json = json.dumps({"data": {"member": {"medallionStatus": "Platinum", "skymiles": 55000}}})
    gemini_fields = [
        {"key": "elite_status",   "label": "Status",  "value": "Platinum",  "confidence": 0.97},
        {"key": "points_balance", "label": "SkyMiles", "value": "55000", "confidence": 0.95},
    ]

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mighty._record_connector_candidates("delta", delta_json, gemini_fields)

    output = buf.getvalue()
    print(output.strip())
    if "data.member.medallionStatus" in output and "data.member.skymiles" in output:
        print("  PASS  Correct paths logged as candidates")
        return True
    else:
        print("  FAIL  Expected paths not found in output")
        return False


# ── Main ─────────────────────────────────────────────────────────────────────

SUITES = {
    "connector":  test_connectors,
    "intercept":  test_intercept_pipeline,
    "health":     test_site_health,
    "candidate":  test_candidate_logging,
}

if __name__ == "__main__":
    args = sys.argv[1:]
    selected = [k for k in SUITES if not args or k in args]

    if not selected:
        print(f"Unknown test(s): {args}. Available: {list(SUITES)}")
        sys.exit(1)

    results = {}
    for name in selected:
        try:
            results[name] = SUITES[name]()
        except Exception as e:
            import traceback
            print(f"\n  ERROR in {name}: {e}")
            traceback.print_exc()
            results[name] = False

    print("\n" + "="*40)
    all_passed = all(results.values())
    for name, ok in results.items():
        print(f"  {'✓' if ok else '✗'}  {name}")
    print("="*40)
    sys.exit(0 if all_passed else 1)
