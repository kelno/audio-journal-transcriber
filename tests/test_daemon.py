"""Lifecycle tests for the default continuous coordinator."""

from typing import TYPE_CHECKING, cast
from unittest.mock import Mock

import pytest

import transcriber.daemon as daemon_module
from transcriber.actions.http_api import ActionHttpServer
from transcriber.config import TranscribeConfig
from transcriber.daemon import TranscriptionDaemon
from transcriber.files.file_watcher import FileWatcher

if TYPE_CHECKING:
    from transcriber.audio_transcriber import AudioTranscriber


def test_continuous_mode_owns_initial_update_and_watcher_lifecycle(
    fake_config: TranscribeConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default coordinator starts infrastructure before its first update."""
    raw_transcriber = Mock()
    transcriber = cast("AudioTranscriber", raw_transcriber)
    raw_server = Mock(spec=ActionHttpServer)
    server_factory = Mock(return_value=raw_server)
    monkeypatch.setattr(daemon_module, "ActionHttpServer", server_factory)
    daemon = TranscriptionDaemon(transcriber, fake_config)

    raw_watcher = Mock(spec=FileWatcher)
    daemon.watcher = cast("FileWatcher", raw_watcher)

    def run_initial_update() -> list[object]:
        raw_server.start.assert_called_once_with()
        raw_watcher.start.assert_called_once_with()
        daemon.stop()
        return []

    raw_transcriber.run.side_effect = run_initial_update

    daemon.run()

    raw_transcriber.run.assert_called_once_with()
    raw_watcher.stop.assert_called_once_with()
    raw_server.stop.assert_called_once_with()
