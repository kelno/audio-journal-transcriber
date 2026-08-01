"""Tests for bundle-title state and normalization."""

import logging

import pytest

from transcriber.bundle_title import BUNDLE_TITLE_MAX_LENGTH, normalize_bundle_title
from transcriber.exception import InvalidBundleTitleException


class TestNormalizeBundleTitle:
    """Verify the shared title policy used by every naming source."""

    def test_replaces_invalid_characters_and_collapses_whitespace(self) -> None:
        """Filesystem-invalid characters become a single readable separator."""
        assert normalize_bundle_title('  Q4<>:"/\\|?*   Planning  ') == "Q4 Planning"

    def test_preserves_unicode_and_valid_punctuation(self) -> None:
        """Natural-language punctuation is retained when filesystems support it."""
        assert normalize_bundle_title("L'été — planning") == "L'été — planning"

    def test_truncates_at_word_boundary(self, caplog: pytest.LogCaptureFixture) -> None:
        """Long titles retain complete words whenever a boundary is available."""
        title = "This is a very long bundle title that exceeds the maximum allowed character length"

        with caplog.at_level(logging.WARNING, logger="transcriber"):
            normalized_title = normalize_bundle_title(title)

        assert normalized_title == "This is a very long bundle title that exceeds the maximum"
        assert len(normalized_title) <= BUNDLE_TITLE_MAX_LENGTH
        assert f"exceeds {BUNDLE_TITLE_MAX_LENGTH} characters and was truncated" in caplog.text
        assert f"to [{normalized_title}]" in caplog.text

    def test_hard_cuts_single_long_word(self) -> None:
        """A title without word boundaries still respects the character limit."""
        assert normalize_bundle_title("x" * 80) == "x" * BUNDLE_TITLE_MAX_LENGTH

    def test_rejects_empty_normalized_title(self) -> None:
        """Invalid characters alone cannot produce a usable title."""
        with pytest.raises(InvalidBundleTitleException, match="empty"):
            normalize_bundle_title('<>:"/\\|?*')
