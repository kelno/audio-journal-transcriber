"""Bundle-title state and cross-platform normalization rules."""

from enum import StrEnum
from typing import Annotated

from pydantic import AfterValidator

from transcriber.exception import InvalidBundleTitleException
from transcriber.logger import logger

BUNDLE_TITLE_MAX_LENGTH = 60

_INVALID_FILESYSTEM_CHARACTERS = frozenset('<>:"/\\|?*')


def validate_requested_bundle_title(title: str) -> str:
    """Reject an empty requested title while preserving its original text."""
    if not title.strip():
        msg = "A requested bundle title cannot be empty"
        raise ValueError(msg)
    return title


type RequestedBundleTitle = Annotated[str, AfterValidator(validate_requested_bundle_title)]


class BundleTitleState(StrEnum):
    """Represent the naming lifecycle and provenance of a bundle title."""

    # The current directory name is provisional and should be replaced by an
    # AI-generated title when a summary is available.
    PENDING = "pending"

    # The current title was generated from the summary and may be invalidated
    # when the recording or its summary context changes.
    GENERATED = "generated"

    # The current title was explicitly supplied by the user and must survive
    # automatic summary regeneration, refreshes, and merges.
    MANUAL = "manual"


def normalize_bundle_title(title: str) -> str:
    """Return a safe, readable title, shortening it at a word boundary."""
    title_without_invalid_characters = "".join(
        " " if character in _INVALID_FILESYSTEM_CHARACTERS or ord(character) < 32 else character for character in title
    )
    normalized_title = " ".join(title_without_invalid_characters.split()).rstrip(". ")

    if not normalized_title:
        raise InvalidBundleTitleException(
            invalid_title=title,
            reason="title is empty after removing invalid filesystem characters",
        )

    if len(normalized_title) <= BUNDLE_TITLE_MAX_LENGTH:
        return normalized_title

    truncated_title = normalized_title[:BUNDLE_TITLE_MAX_LENGTH].rstrip()
    if not normalized_title[BUNDLE_TITLE_MAX_LENGTH].isspace():
        last_word_boundary = truncated_title.rfind(" ")
        if last_word_boundary > 0:
            truncated_title = truncated_title[:last_word_boundary]

    truncated_title = truncated_title.rstrip(". ")
    logger.warning(
        f"Bundle title exceeds {BUNDLE_TITLE_MAX_LENGTH} characters and was truncated "
        f"from [{normalized_title}] to [{truncated_title}]",
    )
    return truncated_title
