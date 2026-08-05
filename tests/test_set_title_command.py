"""Integration tests for the recording set-title command."""

from datetime import datetime, timedelta

from tests.bundle_fixtures import TranscribeBundleFactory
from tests.fake_ai_manager import FakeAIManager
from tests.fake_file_system import FakeFileSystemService
from transcriber.actions.action_request_store import SQLiteActionRequestStore, default_action_request_database_path
from transcriber.audio_transcriber import AudioTranscriber
from transcriber.bundle_title import BundleTitleState
from transcriber.commands.command_interpretation import (
    ArgumentlessCommandInterpretation,
    CommandInterpretation,
    SetTitleCommandArguments,
    SetTitleCommandInterpretation,
)
from transcriber.commands.command_resolution import ActionRequestCommandResolution
from transcriber.commands.command_type import CommandType
from transcriber.config import TranscribeConfig
from transcriber.transcribe_bundle_job import RunCommandsJob


def _title_interpretation(title: str) -> CommandInterpretation:
    """Build a valid structured interpretation for a title command."""
    return SetTitleCommandInterpretation(
        command_type=CommandType.SET_TITLE,
        arguments=SetTitleCommandArguments(title=title),
    )


class TestSetTitleCommand:
    """Verify title command behavior through the command job pipeline."""

    def test_latest_title_wins_without_running_queued_ai_name_job(
        self,
        fake_transcriber: AudioTranscriber,
        fake_ai_manager: FakeAIManager,
        fake_fs: FakeFileSystemService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Pending revisions collapse before one manual rename is performed."""
        first_command = "set the title to Initial title"
        final_command = "actually set the title to Final title"
        bundle = transcribe_bundle_factory(
            bundle_name="recording",
            audio_filename="recording.mp3",
            transcript_text="Transcript",
            summary_text="Summary",
            commands=[first_command, final_command],
        )
        fake_transcriber.ai_manager = fake_ai_manager
        fake_ai_manager.interpret_commands = {
            first_command: _title_interpretation("Initial title"),
            final_command: _title_interpretation("Final title"),
        }
        bundle_cache = {bundle.bundle_id: bundle}

        errored = fake_transcriber.process_jobs(
            fake_transcriber.gather_jobs(bundle_cache),
            bundle_cache,
        )

        assert errored == []
        assert bundle.bundle_name.endswith("Final title")
        assert bundle.metadata.bundle_title_state is BundleTitleState.MANUAL
        assert len(fake_fs.get_operations("rename")) == 1
        assert fake_ai_manager.named_summaries == []
        assert bundle.commands is not None
        assert all(command.executed for command in bundle.commands.commands)

    def test_merge_runs_before_latest_title_and_applies_it_to_target(
        self,
        fake_ai_manager: FakeAIManager,
        fake_config: TranscribeConfig,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """A title spoken with merge follows the recording onto the surviving bundle."""
        target_date = datetime(2025, 1, 15, 10, tzinfo=fake_config.general.timezone)
        target = transcribe_bundle_factory(
            bundle_name="2025-01-15_target",
            audio_filename="Recording 20250115100000.mp3",
            bundle_date=target_date,
        )
        first_title = "set the title to Draft title"
        merge = "merge this with the previous recording"
        final_title = "set the title to Combined planning"
        source = transcribe_bundle_factory(
            bundle_name="2025-01-15_source",
            audio_filename="Recording 20250115120000.mp3",
            bundle_date=target_date + timedelta(hours=2),
            commands=[first_title, merge, final_title],
        )
        assert source.commands is not None
        interpretations = [
            _title_interpretation("Draft title"),
            ArgumentlessCommandInterpretation(command_type=CommandType.MERGE),
            _title_interpretation("Combined planning"),
        ]
        for command, interpretation in zip(source.commands.commands, interpretations, strict=True):
            source.set_command_interpretation(command.id, interpretation)
        bundle_cache = {
            target.bundle_id: target,
            source.bundle_id: source,
        }

        merge_effects = RunCommandsJob(source, dry_run=False).run(fake_ai_manager, fake_config, bundle_cache)

        assert merge_effects is not None
        assert len(merge_effects.changed_bundle_ids) == 1
        merged_target = bundle_cache[merge_effects.changed_bundle_ids[0]]
        RunCommandsJob(merged_target, dry_run=False).run(fake_ai_manager, fake_config, bundle_cache)

        assert source.bundle_id not in bundle_cache
        assert merged_target.bundle_name.endswith("Combined planning")
        assert merged_target.metadata.bundle_title_state is BundleTitleState.MANUAL
        assert merged_target.commands is not None
        assert all(command.executed for command in merged_target.commands.commands)
        title_command = next(command for command in merged_target.commands.commands if command.text == final_title)
        assert isinstance(title_command.resolution, ActionRequestCommandResolution)
        assert title_command.resolution.outcome == "succeeded"
        request = SQLiteActionRequestStore(
            default_action_request_database_path(fake_config.general.store_dir),
        ).get(title_command.resolution.request_id)
        assert request is not None
        assert request.acknowledged_at is not None
