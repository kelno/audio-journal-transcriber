"""Bundle identifier type and creation helpers."""

from typing import Annotated
from uuid import uuid4

from pydantic import StringConstraints

BUNDLE_ID_PATTERN = r"^[0-9a-f]{32}$"

type BundleId = Annotated[str, StringConstraints(pattern=BUNDLE_ID_PATTERN)]


def new_bundle_id() -> BundleId:
    """Create a lowercase UUID4 hex identifier for a new bundle.

    Returns:
        A new persistent bundle identifier.

    """
    return uuid4().hex
