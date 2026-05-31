"""Command handlers for executing command operations.

Contains the implementation logic for each command type.
"""

from __future__ import annotations

from collections.abc import Callable
from types import NotImplementedType

from transcriber.config import TranscribeConfig
from transcriber.logger import logger
from transcriber.transcribe_bundle import TranscribeBundle

# Returns success
CommandHandler = Callable[[TranscribeBundle, TranscribeConfig, str], None]


def command_handler(func: CommandHandler) -> CommandHandler:
    """Decorator to enforce CommandHandler interface compliance."""
    return func


@command_handler
def handle_merge(_bundle: TranscribeBundle, _config: TranscribeConfig, cmd_text: str) -> None:
    """Merge the current recording with the previous one.

    Args:
        bundle: The transcribe bundle to operate on.
        config: The transcribe configuration.
        cmd_text: The original command text.

    """
    # TODO: Implement merge logic
    # Add a configurable maximum merge time? Like 12h by default
    #
    # throw not yet implemented exception
    msg = "Merge command handler not yet implemented"
    raise NotImplementedError(msg)


@command_handler
def handle_delete(bundle: TranscribeBundle, _config: TranscribeConfig, cmd_text: str) -> None:
    """Delete the current recording.

    Args:
        bundle: The transcribe bundle to operate on.
        config: The transcribe configuration.
        cmd_text: The original command text.

    """
    logger.info(f"Running delete command for {bundle} (command text: {cmd_text})")
    bundle.fs_service.delete_directory(bundle.get_bundle_dir())


@command_handler
def handle_unknown(_bundle: TranscribeBundle, _config: TranscribeConfig, cmd_text: str) -> None:
    """Handle unknown command type.

    Args:
        bundle: The transcribe bundle to operate on.
        config: The transcribe configuration.
        cmd_text: The original command text.

    Raises:
        ValueError: Always raised as the command type is unknown.

    """
    msg = f"Unknown command type cannot be executed (command text: {cmd_text})"
    raise ValueError(msg)


@command_handler
def handle_ignore(_bundle: TranscribeBundle, _config: TranscribeConfig, cmd_text: str) -> None:
    """Handle ignore command type.

    Args:
        bundle: The transcribe bundle to operate on.
        config: The transcribe configuration.
        cmd_text: The original command text.

    """
    logger.debug(f"Command {cmd_text} is ignored.")
