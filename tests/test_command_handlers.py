from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

from tests.bundle_fixtures import TranscribeBundleFactory
from tests.fake_file_system import FakeFileSystemService
from transcriber.audio_manipulation import AudioManipulation
from transcriber.commands.command_handlers import handle_merge
from transcriber.config import TranscribeConfig
from transcriber.constants import MULTIPLE_TRANSCRIPTS_SEPARATOR
from transcriber.exception import AbortRemainingBundleJobsException
from transcriber.transcribe_bundle import TranscribeBundle


class TestHandleMerge:
    def test_handle_merge_merges_with_recent_previous_bundle(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        transcribe_bundle_factory: TranscribeBundleFactory,
        monkeypatch: MonkeyPatch,
    ) -> None:
        """Verify merge can join bundles while preserving all command state and other metadata."""
        previous_audio_filename = "Recording 20250115010000.mp3"
        previous_cmd_raw1 = "keep this"
        previous_cmd_raw2 = "execute me too"
        previous_transcript_text = "previous transcript"
        previous_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_previous",
            audio_filename=previous_audio_filename,
            transcript_text="previous transcript",
            summary_text="previous summary",
            commands=[previous_cmd_raw1, previous_cmd_raw2],
            audio_length=30.0,
            commands_executed=False,
        )
        # Manually mark only first command as executed to test mixed states
        assert previous_bundle.commands
        previous_bundle.commands.commands[0].executed = True
        previous_bundle_dir = previous_bundle.get_bundle_dir()
        previous_bundle.commands.write(previous_bundle_dir, fake_fs)

        current_audio_filename = "Recording 20250115090000.mp3"
        current_command_raw1 = "merge this"
        current_command_raw2 = "blah blah"
        current_command_raw3 = "final command"
        current_transcript_text = "current transcript"
        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_meeting",
            audio_filename=current_audio_filename,
            transcript_text=current_transcript_text,
            commands=[current_command_raw1, current_command_raw2, current_command_raw3],
            audio_length=20.0,
            commands_executed=False,
        )
        # Mark only second command as executed
        assert current_bundle.commands
        assert current_bundle.commands.commands[0].executed is False  # our merge command, starts at false
        current_bundle.commands.commands[1].executed = True
        current_bundle_dir = current_bundle.get_bundle_dir()
        current_bundle.commands.write(current_bundle_dir, fake_fs)

        # TODO (for human): proper mocking instead of monkeypatching the method directly!!
        def fake_audio_duration(_: Path) -> float:
            return 20.0

        monkeypatch.setattr(AudioManipulation, "get_audio_duration", fake_audio_duration)

        with pytest.raises(AbortRemainingBundleJobsException):
            handle_merge(current_bundle, fake_config, current_command_raw1)

        assert not fake_fs.directory_exists(current_bundle_dir)

        merged_bundle = TranscribeBundle.from_existing_directory(
            existing_dir=previous_bundle_dir,
            config=fake_config,
            fs_service=fake_fs,
        )

        # Verify audio files were merged
        assert {path.name for path in merged_bundle.source_audios} == {
            previous_audio_filename,
            current_audio_filename,
        }
        assert merged_bundle.metadata.audio_length == 40.0

        # Verify transcripts were concatenated
        assert merged_bundle.transcript is not None
        assert previous_transcript_text in merged_bundle.transcript.text
        assert current_transcript_text in merged_bundle.transcript.text
        assert MULTIPLE_TRANSCRIPTS_SEPARATOR in merged_bundle.transcript.text

        # Verify summary was cleared
        assert merged_bundle.summary is None
        assert merged_bundle.metadata.bundle_name_generated is False

        # Verify all commands were preserved with their execution states
        assert merged_bundle.commands is not None
        commands = merged_bundle.commands.commands
        assert len(commands) == 5
        assert [cmd.text for cmd in commands] == [
            previous_cmd_raw1,
            previous_cmd_raw2,
            current_command_raw1,
            current_command_raw2,
            current_command_raw3,
        ]

        # Verify execution states were preserved from source bundles
        assert commands[0].executed is True  # previous bundle first command
        assert commands[1].executed is False  # previous bundle second command
        assert (
            commands[2].executed is True
        )  # current bundle first command, started as false but now true because it's the merge command that gots executed
        assert commands[3].executed is True  # current bundle second command
        assert commands[4].executed is False  # current bundle third command, never executed, should still be false

    def test_handle_merge_fails_when_previous_bundle_is_too_old(
        self,
        fake_config: TranscribeConfig,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Verify merge rejects a previous bundle that is older than the configured merge window."""
        transcribe_bundle_factory(
            bundle_name="2025-01-14_previous",
            audio_filename="Recording 20250114090000.mp3",
            audio_length=30.0,
        )

        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_meeting",
            audio_filename="Recording 20250115090000.mp3",
            audio_length=20.0,
        )

        with pytest.raises(ValueError, match="No previous bundle found to merge with"):
            handle_merge(current_bundle, fake_config, "merge")

    def test_handle_merge_fails_without_previous_bundle(
        self,
        fake_config: TranscribeConfig,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Verify merge raises a clear error when there is no prior bundle to merge into."""
        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_meeting",
            audio_filename="curr.mp3",
            audio_length=20.0,
        )

        with pytest.raises(ValueError, match="No previous bundle found to merge with"):
            handle_merge(current_bundle, fake_config, "merge")
