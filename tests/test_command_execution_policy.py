"""Tests for policy-driven handling of repeated command types."""

from collections.abc import Callable

import pytest

from tests.bundle_fixtures import TranscribeBundleFactory
from tests.fake_ai_manager import FakeAIManager
from transcriber.actions.action_request_store import SQLiteActionRequestStore, default_action_request_database_path
from transcriber.commands.command import Command
from transcriber.commands.command_execution_policy import CommandExecutionPolicy
from transcriber.commands.command_interpretation import (
    ArgumentlessCommandInterpretation,
    ArgumentlessCommandType,
)
from transcriber.commands.command_registry import COMMAND_REGISTRY
from transcriber.commands.command_resolution import ActionRequestCommandResolution
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

    @pytest.mark.parametrize(
        "command_type",
        [CommandType.MERGE, CommandType.DELETE, CommandType.SET_TITLE],
    )
    def test_action_backed_commands_have_no_direct_handler(self, command_type: CommandType) -> None:
        """State-changing commands cannot bypass the action-request boundary."""
        assert COMMAND_REGISTRY[command_type].handler is None

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

    def test_delete_acknowledges_request_when_command_origin_is_removed(
        self,
        fake_ai_manager: FakeAIManager,
        fake_config: TranscribeConfig,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """A successful delete can acknowledge without writing a terminal command receipt."""
        bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_delete-action",
            audio_filename="recording.mp3",
            commands=["delete this recording"],
        )
        _set_all_command_types(bundle, CommandType.DELETE)
        assert bundle.commands is not None
        command = bundle.commands.commands[0]
        bundle_cache = {bundle.bundle_id: bundle}

        effects = RunCommandsJob(bundle, dry_run=False).run(
            fake_ai_manager,
            fake_config,
            bundle_cache,
        )

        assert bundle.bundle_id not in bundle_cache
        assert effects is not None
        assert effects.removed_bundle_ids == (bundle.bundle_id,)
        assert isinstance(command.resolution, ActionRequestCommandResolution)
        request = SQLiteActionRequestStore(
            default_action_request_database_path(fake_config.general.store_dir),
        ).get(command.resolution.request_id)
        assert request is not None
        assert request.status == "succeeded"
        assert request.acknowledged_at is not None
