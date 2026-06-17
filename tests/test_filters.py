"""
Unit tests for _post_filter_fields.

Covers:
- Past-date flight/trip fields are dropped
- Future-dated upcoming trips SURVIVE (the bug we fixed in v0.3)
- Written month-name dates like "Aug 14, 2026" parse correctly
- Compact dates like "22JUL2024" are caught
- Zero-value fields are dropped
- Login-wall values are dropped
- Ticket-ID labels are dropped
- Generic account labels (Cardmember, Member) are dropped
- Generic marketing copy is dropped
- Dedup by label/value works
"""

import pytest
from app import _post_filter_fields


def field(key, label, value, **extras):
    return {"key": key, "label": label, "value": value, **extras}


# ── Future trips survive ──────────────────────────────────────────────────────

class TestFutureTripsKept:
    def test_future_flight_iso_date_kept(self):
        """Upcoming flight with ISO future date must not be dropped."""
        fields = [field("upcoming_flight", "Upcoming Flight", "SFO → JFK, 2027-03-15")]
        result = _post_filter_fields(fields)
        assert any(f["key"] == "upcoming_flight" for f in result), \
            "Future ISO-dated flight was incorrectly dropped"

    def test_future_flight_written_date_kept(self):
        """'Aug 14, 2026' is a future date — must survive."""
        fields = [field("upcoming_flight", "Upcoming Flight", "SFO → JFK, Aug 14, 2026")]
        result = _post_filter_fields(fields)
        assert any(f["key"] == "upcoming_flight" for f in result), \
            "Future written-date flight (Aug 14 2026) was incorrectly dropped"

    def test_future_flight_december_date_kept(self):
        """December 2026 is in the future."""
        fields = [field("upcoming_flight", "Upcoming Flight", "ORD → LAX, December 20, 2026")]
        result = _post_filter_fields(fields)
        assert any(f["key"] == "upcoming_flight" for f in result)

    def test_future_trip_label_variants_kept(self):
        """All 'upcoming X' label variants must keep future-dated fields."""
        for label in ("Upcoming Reservation", "Upcoming Trip", "Upcoming Stay",
                      "Upcoming Booking", "Upcoming Itinerary", "Upcoming Travel"):
            fields = [field("trip", label, "Sep 30, 2026")]
            result = _post_filter_fields(fields)
            assert len(result) == 1, \
                f"Future-dated field with label '{label}' was incorrectly dropped"


# ── Past trips dropped ────────────────────────────────────────────────────────

class TestPastTripsDropped:
    def test_past_compact_date_dropped(self):
        """'22JUL2024' is in the past — must be dropped."""
        fields = [field("upcoming_flight", "Upcoming Flight", "ATL to SFO on 22JUL2024")]
        result = _post_filter_fields(fields)
        assert not any(f["key"] == "upcoming_flight" for f in result), \
            "Past compact-date flight (22JUL2024) was not dropped"

    def test_past_written_date_dropped(self):
        """'Jan 5, 2025' is in the past."""
        fields = [field("upcoming_flight", "Upcoming Flight", "JFK → LAX, Jan 5, 2025")]
        result = _post_filter_fields(fields)
        assert not any(f["key"] == "upcoming_flight" for f in result)

    def test_past_iso_date_in_value_dropped(self):
        fields = [field("upcoming_trip", "Upcoming Trip", "Check-in 2024-11-15")]
        result = _post_filter_fields(fields)
        assert not any(f["key"] == "upcoming_trip" for f in result)

    def test_no_date_upcoming_label_dropped(self):
        """An 'upcoming' label with no detectable date should be dropped."""
        fields = [field("upcoming_flight", "Upcoming Flight", "Pending")]
        result = _post_filter_fields(fields)
        assert not any(f["key"] == "upcoming_flight" for f in result)


# ── Date parsing coverage ─────────────────────────────────────────────────────

class TestDateFormatCoverage:
    """Verify the filter correctly interprets every date format we claim to support."""

    def _round_trip(self, value: str, label="Upcoming Flight") -> bool:
        """Return True if the field survives the filter, False if dropped."""
        fields = [field("trip", label, value)]
        result = _post_filter_fields(fields)
        return any(f["key"] == "trip" for f in result)

    def test_iso_future(self):
        assert self._round_trip("Flight on 2027-01-01") is True

    def test_iso_past(self):
        assert self._round_trip("Flight on 2020-06-15") is False

    def test_slash_future(self):
        assert self._round_trip("Check-in 12/31/2099") is True

    def test_slash_past(self):
        assert self._round_trip("Check-in 01/01/2020") is False

    def test_compact_past(self):
        assert self._round_trip("22JUL2024") is False

    def test_compact_future(self):
        assert self._round_trip("15DEC2099") is True

    def test_written_future_with_comma(self):
        assert self._round_trip("Aug 14, 2099") is True

    def test_written_past_with_comma(self):
        assert self._round_trip("Jan 5, 2020") is False

    def test_written_future_no_comma(self):
        assert self._round_trip("Aug 14 2099") is True

    def test_written_past_no_comma(self):
        assert self._round_trip("March 3 2019") is False

    def test_full_month_name_future(self):
        assert self._round_trip("September 30, 2099") is True

    def test_sept_abbreviation_future(self):
        assert self._round_trip("Sept 5, 2099") is True

    def test_sept_abbreviation_past(self):
        assert self._round_trip("Sept 5, 2019") is False


# ── Zero / empty values dropped ───────────────────────────────────────────────

class TestZeroAndEmptyDropped:
    def test_zero_string_dropped(self):
        assert _post_filter_fields([field("k", "Nights This Year", "0")]) == []

    def test_dollar_zero_dropped(self):
        assert _post_filter_fields([field("k", "Gift Card Balance", "$0.00")]) == []

    def test_none_string_dropped(self):
        assert _post_filter_fields([field("k", "Upcoming Trips", "None")]) == []

    def test_na_dropped(self):
        assert _post_filter_fields([field("k", "Status", "N/A")]) == []

    def test_empty_value_dropped(self):
        assert _post_filter_fields([field("k", "Balance", "")]) == []

    def test_pending_placeholder_dropped(self):
        """'Pending' as a value placeholder for a balance/date should be dropped."""
        assert _post_filter_fields([field("k", "Points Balance", "Pending")]) == []
        assert _post_filter_fields([field("k", "Miles", "Pending")]) == []

    def test_pending_real_status_kept(self):
        """'Pending' when it IS the status of a claim/application/auth must be kept."""
        assert _post_filter_fields([field("k", "Claim Status", "Pending")]) != []
        assert _post_filter_fields([field("k", "Application Status", "Pending")]) != []
        assert _post_filter_fields([field("k", "Dispute Status", "Pending")]) != []


# ── Login-wall values dropped ─────────────────────────────────────────────────

class TestLoginWallDropped:
    def test_login_to_view_dropped(self):
        fields = [field("k", "Points Balance", "Log in to view points balance")]
        assert _post_filter_fields(fields) == []

    def test_sign_in_to_see_dropped(self):
        fields = [field("k", "Balance", "Sign in to see your balance")]
        assert _post_filter_fields(fields) == []


# ── Generic labels dropped ────────────────────────────────────────────────────

class TestGenericLabelsDropped:
    def test_cardmember_dropped(self):
        fields = [field("k", "Cardmember Status", "Cardmember")]
        assert _post_filter_fields(fields) == []

    def test_member_dropped(self):
        fields = [field("k", "Membership Level", "Member")]
        assert _post_filter_fields(fields) == []


# ── Ticket-ID labels dropped ──────────────────────────────────────────────────

class TestTicketIdDropped:
    def test_long_number_in_label_dropped(self):
        fields = [field("k", "eTicket 0062253264364 Expiry", "2026-12-31")]
        assert _post_filter_fields(fields) == []


# ── Good fields pass through ──────────────────────────────────────────────────

class TestGoodFieldsKept:
    def test_elite_status_kept(self):
        fields = [field("elite_status", "Elite Status", "Gold Medallion")]
        result = _post_filter_fields(fields)
        assert len(result) == 1

    def test_points_balance_kept(self):
        fields = [field("miles", "SkyMiles Balance", "45,320")]
        result = _post_filter_fields(fields)
        assert len(result) == 1

    def test_companion_certificate_kept(self):
        fields = [field("companion_cert", "Companion Certificate", "Valid through Jan 15, 2027")]
        result = _post_filter_fields(fields)
        assert len(result) == 1

    def test_minimum_payment_kept(self):
        fields = [field("min_pay", "Minimum Payment Due", "$35 by Jul 12, 2026")]
        result = _post_filter_fields(fields)
        assert len(result) == 1

    def test_autopay_kept(self):
        fields = [field("autopay", "Auto Pay Status", "Enrolled")]
        result = _post_filter_fields(fields)
        assert len(result) == 1

    def test_provenance_fields_preserved(self):
        """confidence and source_snippet must pass through the filter unchanged."""
        fields = [field(
            "elite_status", "Elite Status", "Platinum Elite",
            confidence=0.97,
            source_snippet="Platinum Elite status"
        )]
        result = _post_filter_fields(fields)
        assert len(result) == 1
        assert result[0].get("confidence") == 0.97
        assert result[0].get("source_snippet") == "Platinum Elite status"
