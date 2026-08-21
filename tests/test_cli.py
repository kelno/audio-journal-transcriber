"""Tests for command-line run-mode selection."""

import pytest

from transcriber.cli import create_argument_parser
from transcriber.config import HttpConfig


def test_default_mode_watches_and_serves_continuously() -> None:
    """Continuous operation is represented by the absence of the exceptional flag."""
    arguments = create_argument_parser().parse_args([])

    assert arguments.once is False
    assert HttpConfig().enabled is True


def test_once_selects_one_update_cycle() -> None:
    """The explicit one-shot flag bypasses the daemon lifecycle."""
    arguments = create_argument_parser().parse_args(["--once"])

    assert arguments.once is True


def test_removed_daemon_flag_is_rejected() -> None:
    """The old opt-in flag is not retained as an alpha compatibility alias."""
    with pytest.raises(SystemExit):
        create_argument_parser().parse_args(["--daemon"])
