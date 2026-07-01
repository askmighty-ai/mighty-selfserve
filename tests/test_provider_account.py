"""Tests for the provider account model."""

import os
import sys

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret-key-do-not-use-in-production")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mighty.provider_account import (
    EXTRACTION_COMPLETE,
    EXTRACTION_FAILED,
    EXTRACTION_NOT_STARTED,
    EXTRACTION_PENDING,
    ProviderAccount,
    has_normalized_data,
    infer_extraction_status,
    is_synced,
    normalize_data_source,
)


def test_has_normalized_data():
    assert has_normalized_data([]) is False
    assert has_normalized_data([{"value": "—"}]) is False
    assert has_normalized_data([{"value": "42,000"}]) is True


def test_is_synced_any_adapter():
    fields = [{"label": "Points", "value": "12,000"}]
    assert is_synced(fields) is True
    acct = ProviderAccount(source="amex", normalized_fields=fields, data_source="api")
    assert acct.is_synced is True
    acct_ext = ProviderAccount(source="amex", normalized_fields=fields, data_source="extension")
    assert acct_ext.is_synced is True


def test_infer_extraction_status():
    assert infer_extraction_status([]) == EXTRACTION_NOT_STARTED
    assert infer_extraction_status([{"value": "1"}]) == EXTRACTION_COMPLETE
    assert infer_extraction_status([], sync_status="login_required") == EXTRACTION_FAILED
    assert infer_extraction_status([], explicit=EXTRACTION_PENDING) == EXTRACTION_PENDING


def test_normalize_data_source():
    assert normalize_data_source("extension") == "extension"
    assert normalize_data_source("railway") == "railway"
    assert normalize_data_source(None) is None
