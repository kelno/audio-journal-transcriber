"""Tests for extracting timestamps from recording filenames."""

from zoneinfo import ZoneInfo

import pytest

from transcriber.filename_timestamp import extract_date_from_recording_filename

LOCAL_TIMEZONE = ZoneInfo("Europe/Brussels")


@pytest.mark.parametrize(
    ("filename", "expected_isoformat"),
    [
        ("Recording 20260801143000.m4a", "2026-08-01T14:30:00+02:00"),
        ("Recording_20260801143000.m4a", "2026-08-01T14:30:00+02:00"),
        ("2026-08-01_recording.m4a", "2026-08-01T00:00:00+02:00"),
        ("2026_08_01_recording.m4a", "2026-08-01T00:00:00+02:00"),
        ("memo.2026.08.01.m4a", "2026-08-01T00:00:00+02:00"),
        ("memo_20260801.m4a", "2026-08-01T00:00:00+02:00"),
        ("Voice 002_W_20230102_213412.m4a", "2023-01-02T21:34:12+01:00"),
        ("meeting_20260801-14-30-00.m4a", "2026-08-01T14:30:00+02:00"),
        ("2026-08-01_14-30-00.m4a", "2026-08-01T14:30:00+02:00"),
        ("2026_08_01 14_30_00.m4a", "2026-08-01T14:30:00+02:00"),
        ("2026.08.01 14.30.00.m4a", "2026-08-01T14:30:00+02:00"),
        ("2026-08-01T14:30:00.m4a", "2026-08-01T14:30:00+02:00"),
        ("interview_20260801_1430.m4a", "2026-08-01T14:30:00+02:00"),
        ("2026-08-01T14:30Z.m4a", "2026-08-01T14:30:00+00:00"),
        ("GMT20260801-143000_call.mp3", "2026-08-01T14:30:00+00:00"),
    ],
)
def test_extract_date_supports_common_year_first_formats(
    filename: str,
    expected_isoformat: str,
) -> None:
    """Common recorder separators, positions, and timezones are recognized."""
    result = extract_date_from_recording_filename(filename, LOCAL_TIMEZONE)

    assert result is not None
    assert result.isoformat() == expected_isoformat


@pytest.mark.parametrize(
    "filename",
    [
        "recording_08-01-2026.m4a",  # Ambiguous day/month order; year is not first.
        "recording_01-08-26.m4a",  # Ambiguous order and a two-digit year.
        "recording_2026-08_01.m4a",  # Date separators are inconsistent.
        "recording_2026-08-01_14-30_00.m4a",  # Time separators are inconsistent.
        "customer1232026080114300099.m4a",  # Timestamp is embedded in a longer number.
        "recording_2026-02-30.m4a",  # February 30 is not a valid calendar date.
        "recording_20260801_996000.m4a",  # Hour and minute values are invalid.
        "recording_2026-08-01_14-30-99.m4a",  # Second value is invalid.
        "recording_20260801_143000+0530.m4a",  # Numeric UTC offsets are unsupported.
        "recording_2026-08-01T14:30:00-05:00.m4a",  # Numeric UTC offsets are unsupported.
        "recording_1722515400.m4a",  # Unix timestamps are intentionally unsupported.
        "recording_without_a_timestamp.m4a",  # No timestamp-shaped text is present.
        "recording_20260801_to_20260802.m4a",  # Two equally ranked dates conflict.
    ],
)
def test_extract_date_rejects_unsafe_or_ambiguous_formats(filename: str) -> None:
    """Ambiguous, malformed, embedded, and conflicting timestamps are rejected."""
    assert extract_date_from_recording_filename(filename, LOCAL_TIMEZONE) is None


def test_extract_date_accepts_repeated_equivalent_timestamp() -> None:
    """Repeating the same timestamp is not treated as a conflict."""
    result = extract_date_from_recording_filename(
        "20260801_copy_of_20260801.m4a",
        LOCAL_TIMEZONE,
    )

    assert result is not None
    assert result.isoformat() == "2026-08-01T00:00:00+02:00"
