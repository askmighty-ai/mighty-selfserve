"""
Test harness bootstrap.

Sets the minimum environment variables so app.py can be imported without a real
database, Gemini key, or production SECRET_KEY.  Import this before any test
that needs symbols from app.py — pytest loads conftest.py automatically.

All tests that call Gemini are decorated with:
    @pytest.mark.skipif(not os.environ.get("GEMINI_API_KEY"), reason="requires GEMINI_API_KEY")
so the unit tests always run and the integration tests are opt-in.
"""

import os
import sys

# Must be set before importing app
os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
os.environ.setdefault("DATABASE_PATH", "/tmp/mighty_test.db")

# Add project root to path so `import app` resolves
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
