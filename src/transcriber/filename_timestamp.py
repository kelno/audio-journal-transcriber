"""Discover and validate timestamps embedded in recording filenames."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo

from transcriber.logger import logger

# Filename timestamp parsing has four separate stages:
#
# 1. The regex fragments below discover timestamp-shaped text anywhere in a name.
# 2. ``datetime`` validates the captured numbers (so 2026-02-30 is rejected).
# 3. Candidates are ranked by explicit timezone and then by precision.
# 4. Equally ranked, conflicting values are rejected instead of guessed.

# Precision is stored as an ordered value so a complete timestamp wins over a date
# found elsewhere in the same filename.
_DATE_PRECISION = 1
_MINUTE_PRECISION = 2
_SECOND_PRECISION = 3

# VERBOSE allows whitespace and comments in composed regexes; IGNORECASE accepts
# both ``GMT`` and ``gmt`` as UTC markers.
_REGEX_FLAGS = re.IGNORECASE | re.VERBOSE

# These negative lookaheads stop shorter patterns from accepting part of a longer,
# malformed timestamp. For example, a bad ``20260801_996000`` must not fall back to
# the otherwise-valid date portion ``20260801``.
_TIMESTAMP_END = r"(?!\d|[:._-]\d{2}|[+-]\d{2}:?\d{2})"
_DATE_END = r"(?!\d|[T _-]\d{2}(?:[:._-]?\d{2}))"

# Named regex groups (``?P<year>`` and so on) let every supported shape feed the
# same validation code. Compact means no separators: YYYYMMDD and HHMM[SS].
_COMPACT_DATE = r"(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})"

# ``(?P<date_separator>[-_.])`` captures the first separator under the name
# ``date_separator``. ``(?P=date_separator)`` is a backreference: it must match the
# exact character captured earlier. This accepts 2026-08-01 and 2026_08_01, while
# rejecting mixed separators such as 2026-08_01.
_SEPARATED_DATE = (
    r"(?P<year>\d{4})(?P<date_separator>[-_.])"
    r"(?P<month>\d{2})(?P=date_separator)(?P<day>\d{2})"
)
_COMPACT_TIME = r"(?P<hour>\d{2})(?P<minute>\d{2})(?P<second>\d{2})?"

# The time separator uses the same capture-and-backreference rule as the date.
_SEPARATED_TIME = (
    r"(?P<hour>\d{2})(?P<time_separator>[:._-])(?P<minute>\d{2})"
    r"(?:(?P=time_separator)(?P<second>\d{2}))?"
)

# A trailing ``Z`` explicitly marks UTC. A leading ``GMT`` is handled in
# _timestamp_pattern because it appears before, rather than after, the timestamp.
# Numeric offsets are intentionally unsupported until a recorder use case requires
# them; _TIMESTAMP_END prevents silently treating such a timestamp as local time.
_UTC_SUFFIX = r"(?P<utc_suffix>Z)?"


@dataclass(frozen=True, slots=True)
class _FilenameTimestampPattern:
    """One discoverable timestamp shape assembled from the shared grammar."""

    name: str
    regex: re.Pattern[str]
    has_time: bool


@dataclass(frozen=True, slots=True)
class _FilenameTimestampCandidate:
    """A validated match plus the metadata needed to resolve competing matches."""

    value: datetime
    precision: int
    has_explicit_timezone: bool
    position: int
    matched_text: str

    @property
    def rank(self) -> tuple[bool, int]:
        """Prefer timezone-qualified values, followed by greater precision."""
        return (self.has_explicit_timezone, self.precision)


def _timestamp_pattern(
    name: str,
    date_pattern: str,
    datetime_separator: str,
    time_pattern: str,
) -> _FilenameTimestampPattern:
    """Build a datetime pattern from shared date, separator, and time fragments.

    The digit boundary prevents extracting a timestamp from inside a longer numeric
    identifier. Leading ``GMT`` and trailing ``Z`` markers are captured independently
    so UTC intent is preserved instead of being replaced with local time.
    """
    return _FilenameTimestampPattern(
        name=name,
        regex=re.compile(
            rf"""
            (?<!\d)(?P<gmt>GMT)?
            {date_pattern}{datetime_separator}{time_pattern}
            {_UTC_SUFFIX}{_TIMESTAMP_END}
            """,
            _REGEX_FLAGS,
        ),
        has_time=True,
    )


def _date_pattern(name: str, date_pattern: str) -> _FilenameTimestampPattern:
    """Build a date-only pattern that cannot consume part of a longer timestamp."""
    return _FilenameTimestampPattern(
        name=name,
        regex=re.compile(
            rf"(?<!\d){date_pattern}{_DATE_END}",
            _REGEX_FLAGS,
        ),
        has_time=False,
    )


# Combining fragments covers compact and separated recorder conventions without one
# opaque, catch-all regex. Every format exposes the same named fields, so configured
# formats can later join this discovery stage without changing validation or ranking.
_FILENAME_TIMESTAMP_PATTERNS = (
    _timestamp_pattern(
        name="separated date and separated time",
        date_pattern=_SEPARATED_DATE,
        datetime_separator=r"[T _-]",
        time_pattern=_SEPARATED_TIME,
    ),
    _timestamp_pattern(
        name="separated date and compact time",
        date_pattern=_SEPARATED_DATE,
        datetime_separator=r"[T _-]",
        time_pattern=_COMPACT_TIME,
    ),
    _timestamp_pattern(
        name="compact date and separated time",
        date_pattern=_COMPACT_DATE,
        datetime_separator=r"[T _-]",
        time_pattern=_SEPARATED_TIME,
    ),
    _timestamp_pattern(
        name="compact date and compact time",
        date_pattern=_COMPACT_DATE,
        datetime_separator=r"[T _-]?",
        time_pattern=_COMPACT_TIME,
    ),
    _date_pattern(
        name="separated date",
        date_pattern=_SEPARATED_DATE,
    ),
    _date_pattern(
        name="compact date",
        date_pattern=_COMPACT_DATE,
    ),
)


def _get_timestamp_timezone(match: re.Match[str], fallback: ZoneInfo) -> tuple[tzinfo, bool]:
    """Resolve an explicit timezone, or use the configured local fallback.

    The returned boolean distinguishes a timezone stated in the filename from the
    fallback. Ranking uses that distinction to prefer the recorder's explicit value.
    """
    groups = match.groupdict()
    is_gmt = groups.get("gmt") is not None
    has_utc_suffix = groups.get("utc_suffix") is not None

    if is_gmt or has_utc_suffix:
        return UTC, True

    return fallback, False


def _candidate_from_match(
    pattern: _FilenameTimestampPattern,
    match: re.Match[str],
    fallback_timezone: ZoneInfo,
) -> _FilenameTimestampCandidate:
    """Convert captured fields into a validated, ranked candidate.

    Constructing ``datetime`` is the validation step: impossible calendar dates and
    times raise ``ValueError`` and never become candidates.
    """
    groups = match.groupdict()
    matched_timezone, has_explicit_timezone = _get_timestamp_timezone(match, fallback_timezone)
    second_text = groups.get("second")
    precision = _DATE_PRECISION
    if pattern.has_time:
        precision = _SECOND_PRECISION if second_text is not None else _MINUTE_PRECISION

    value = datetime(
        year=int(groups["year"]),
        month=int(groups["month"]),
        day=int(groups["day"]),
        hour=int(groups.get("hour") or 0),
        minute=int(groups.get("minute") or 0),
        second=int(second_text or 0),
        tzinfo=matched_timezone,
    )
    return _FilenameTimestampCandidate(
        value=value,
        precision=precision,
        has_explicit_timezone=has_explicit_timezone,
        position=match.start(),
        matched_text=match.group(),
    )


def extract_date_from_recording_filename(
    filename: str,
    tz: ZoneInfo,
) -> datetime | None:
    """Extract the best unambiguous year-first timestamp from a filename.

    All supported shapes are searched because a filename may contain descriptive
    text before its timestamp. A timezone-qualified match outranks a local one, and
    a timestamp outranks a date-only match. Conflicting best matches return ``None``.
    """
    candidates: list[_FilenameTimestampCandidate] = []
    for pattern in _FILENAME_TIMESTAMP_PATTERNS:
        for match in pattern.regex.finditer(filename):
            try:
                candidates.append(_candidate_from_match(pattern, match, tz))
            except ValueError:
                logger.warning(
                    f"Filename {filename} contains an invalid {pattern.name}: {match.group()}",
                )

    if not candidates:
        return None

    # Lower-precision matches often overlap a full timestamp. Ranking removes those
    # expected duplicates before testing whether the filename is truly ambiguous.
    best_rank = max(candidate.rank for candidate in candidates)
    best_candidates = [candidate for candidate in candidates if candidate.rank == best_rank]
    distinct_values = {candidate.value for candidate in best_candidates}
    if len(distinct_values) > 1:
        matches = ", ".join(repr(candidate.matched_text) for candidate in best_candidates)
        logger.warning(f"Filename {filename} contains conflicting timestamps: {matches}")
        return None

    return min(best_candidates, key=lambda candidate: candidate.position).value
