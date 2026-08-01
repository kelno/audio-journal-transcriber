"""Execution policies for repeated commands of the same type."""

from enum import StrEnum


class CommandExecutionPolicy(StrEnum):
    """Define how commands of one type are selected for execution."""

    # Execute the first eligible command and treat later commands of the same
    # type as duplicates, including across processing runs.
    ONCE_PER_BUNDLE = "once_per_bundle"

    # Treat pending commands as revisions of the same intent. Supersede earlier
    # pending revisions and execute only the final one. A new pending revision
    # may execute even if an older command of the same type ran previously.
    LATEST_PENDING = "latest_pending"
