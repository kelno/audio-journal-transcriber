"""Tests for policy-driven handling of repeated command types."""

from collections.abc import Callable

import pytest

from tests.bundle_fixtures import TranscribeBundleFactory
from tests.fake_ai_manager import FakeAIManager
from transcriber.commands.command import Command
from transcriber.commands.command_execution_policy import CommandExecutionPolicy
from transcriber.commands.command_interpretation import (
    ArgumentlessCommandInterpretation,
    ArgumentlessCommandType,
)
from transcriber.commands.command_registry import COMMAND_REGISTRY
from transcriber.commands.command_type import CommandType
from transcriber.config import TranscribeConfig
from transcriber.transcribe_bundle import BundleCache, TranscribeBundle
from transcriber.transcribe_bundle_job import RunCommandsJob

CommandRecorder = Callable[[TranscribeBundle, TranscribeConfig, BundleCache, Command], None]


def _recording_handler(calls: list[str]) -> CommandRecorder:
    """Return a handler that records command text without external side effects."""

    def handler(
        _bundle: TranscribeBundle,
        _config: TranscribeConfig,
        _bundle_cache: BundleCache,
        command: Command,
    ) -> None:
        calls.append(command.text)

    return handler


def _set_all_command_types(bundle: TranscribeBundle, command_type: ArgumentlessCommandType) -> None:
    """Persist one matched type for every command in a test bundle."""
    assert bundle.commands is not None
    for command in bundle.commands.commands:
        bundle.set_command_interpretation(
            command.id,
            ArgumentlessCommandInterpretation(command_type=command_type),
        )


class TestCommandExecutionPolicy:
    """Verify command selection without relying on a specific command handler."""

    def test_once_per_bundle_keeps_existing_first_command_behavior(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_ai_manager: FakeAIManager,
        fake_config: TranscribeConfig,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """The default policy executes the first command and skips later duplicates."""
        bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_once-policy",
            audio_filename="recording.mp3",
            commands=["first", "second", "third"],
        )
        _set_all_command_types(bundle, CommandType.IGNORE)
        calls: list[str] = []
        definition = COMMAND_REGISTRY[CommandType.IGNORE]
        monkeypatch.setattr(definition, "handler", _recording_handler(calls))

        RunCommandsJob(bundle, dry_run=False).run(
            fake_ai_manager,
            fake_config,
            {bundle.bundle_id: bundle},
        )

        assert calls == ["first"]
        assert bundle.commands is not None
        assert all(command.executed for command in bundle.commands.commands)

    def test_latest_pending_executes_only_last_pending_command(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_ai_manager: FakeAIManager,
        fake_config: TranscribeConfig,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Earlier pending commands are superseded without invoking their handlers."""
        bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_latest-policy",
            audio_filename="recording.mp3",
            commands=["already applied", "intermediate", "final choice"],
        )
        _set_all_command_types(bundle, CommandType.IGNORE)
        assert bundle.commands is not None
        bundle.set_command_executed(bundle.commands.commands[0].id)

        calls: list[str] = []
        definition = COMMAND_REGISTRY[CommandType.IGNORE]
        monkeypatch.setattr(definition, "execution_policy", CommandExecutionPolicy.LATEST_PENDING)
        monkeypatch.setattr(definition, "handler", _recording_handler(calls))

        RunCommandsJob(bundle, dry_run=False).run(
            fake_ai_manager,
            fake_config,
            {bundle.bundle_id: bundle},
        )

        assert calls == ["final choice"]
        assert all(command.executed for command in bundle.commands.commands)

    def test_set_title_uses_latest_pending_policy(self) -> None:
        """The title command opts into revision-style deduplication."""
        assert COMMAND_REGISTRY[CommandType.SET_TITLE].execution_policy is CommandExecutionPolicy.LATEST_PENDING
