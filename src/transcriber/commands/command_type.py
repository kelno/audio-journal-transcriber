"""Command interpreter for processing transcription commands.

Defines command types and metadata for matching user intents to specific actions.
"""

from __future__ import annotations

from enum import Enum


class CommandType(Enum):
    """Enumeration of all possible command types."""

    MERGE = "merge"
    DELETE = "delete"
    UNKNOWN = "unknown"
    IGNORE = "ignore"
