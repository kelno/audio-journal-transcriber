# pyright: reportUnusedParameter=false
#
"""Command handlers for executing command operations.

Contains the implementation logic for each command type.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from transcriber.logger import logger

if TYPE_CHECKING:
    from transcriber.config import TranscribeConfig
    from transcriber.transcribe_bundle import TranscribeBundle


def handle_merge(bundle: TranscribeBundle, config: TranscribeConfig) -> None:
    """Merge the current recording with the previous one.

    Args:
        bundle: The transcribe bundle to operate on.
        config: The transcribe configuration.

    Raises:
        NotImplementedError: This handler is not yet implemented.

    """
    # TODO: Implement merge logic
    logger.warning("Merge command handler not yet implemented")


def handle_delete(bundle: TranscribeBundle, config: TranscribeConfig) -> None:
    """Delete the current recording.

    Args:
        bundle: The transcribe bundle to operate on.
        config: The transcribe configuration.

    Raises:
        NotImplementedError: This handler is not yet implemented.

    """
    # TODO: Implement delete logic
    logger.warning("Delete command handler not yet implemented")


def handle_unknown(bundle: TranscribeBundle, config: TranscribeConfig) -> None:
    """Handle unknown command type.

    Args:
        bundle: The transcribe bundle to operate on.
        config: The transcribe configuration.

    Raises:
        ValueError: Always raised as the command type is unknown.

    """
    msg = "Unknown command type cannot be executed"
    raise ValueError(msg)
