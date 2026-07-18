import pytest

from tests.bundle_fixtures import TranscribeBundleFactory
from tests.fake_audio_service import FakeAudioService
from tests.fake_file_system import FakeFileSystemService
from transcriber.commands.command_handlers import handle_merge
from transcriber.config import TranscribeConfig
from transcriber.constants import MULTIPLE_TRANSCRIPTS_SEPARATOR
from transcriber.exception import AbortRemainingBundleJobsException, NoPreviousBundleException
from transcriber.transcribe_bundle import TranscribeBundle


class TestHandleMerge:
    def test_handle_merge_merges_with_recent_previous_bundle(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        transcribe_bundle_factory: TranscribeBundleFactory,
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
            transcript_model_used="model-a",
            keep_forever=True,
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
            transcript_model_used="model-b",
            keep_forever=False,
        )
        # Mark only second command as executed
        assert current_bundle.commands
        assert current_bundle.commands.commands[0].executed is False  # our merge command, starts at false
        current_bundle.commands.commands[1].executed = True
        current_bundle_dir = current_bundle.get_bundle_dir()
        current_bundle.commands.write(current_bundle_dir, fake_fs)

        with pytest.raises(AbortRemainingBundleJobsException):
            handle_merge(current_bundle, fake_config, current_command_raw1)

        assert not fake_fs.directory_exists(current_bundle_dir)

        merged_bundle = TranscribeBundle.from_existing_directory(
            existing_dir=previous_bundle_dir,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )

        # Verify audio files were merged
        assert {path.name for path in merged_bundle.source_audios} == {
            previous_audio_filename,
            current_audio_filename,
        }
        assert merged_bundle.metadata.audio_length == (30.0 + 20.0)

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

        # Verify transcript model usage was merged (both models present)
        merged_model = merged_bundle.metadata.transcript_model_used
        assert merged_model is not None
        assert "model-a" in merged_model
        assert "model-b" in merged_model

        # Verify keep_forever is promoted to True when either source bundle is kept forever
        assert merged_bundle.metadata.keep_forever is True

    def test_handle_merge_merges_transcript_model_and_keep_forever_edge_cases(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Verify transcript_model_used dedup/None handling and keep_forever promotion."""
        # Previous has no model, current has one -> model carried over from current
        previous_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_previous",
            audio_filename="Recording 20250115010000.mp3",
            transcript_text="previous transcript",
            audio_length=30.0,
            transcript_model_used=None,
            keep_forever=False,
        )
        # Current reuses the same model as previous would have, plus keep_forever True
        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_meeting",
            audio_filename="Recording 20250115090000.mp3",
            transcript_text="current transcript",
            audio_length=20.0,
            transcript_model_used="model-a",
            keep_forever=True,
        )

        previous_bundle_dir = previous_bundle.get_bundle_dir()

        with pytest.raises(AbortRemainingBundleJobsException):
            handle_merge(current_bundle, fake_config, "merge")

        merged_bundle = TranscribeBundle.from_existing_directory(
            existing_dir=previous_bundle_dir,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )

        # Model carried over from current since previous had none
        assert merged_bundle.metadata.transcript_model_used == "model-a"
        # keep_forever promoted to True because current bundle set it
        assert merged_bundle.metadata.keep_forever is True

        # Now merge again with a bundle that reuses the same model and no keep_forever.
        # Its timestamp (12:00) must be strictly later than the merged previous
        # bundle's first audio (01:00) and within the 12h merge window.
        third_model = "model-a"
        third_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_later",
            audio_filename="Recording 20250115120000.mp3",
            transcript_text="third transcript",
            audio_length=10.0,
            transcript_model_used=third_model,
            keep_forever=False,
        )
        third_bundle_dir = third_bundle.get_bundle_dir()

        with pytest.raises(AbortRemainingBundleJobsException):
            handle_merge(third_bundle, fake_config, "merge")

        twice_merged = TranscribeBundle.from_existing_directory(
            existing_dir=previous_bundle_dir,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )

        # Duplicate model is not appended again (substring check prevents duplication)
        assert twice_merged.metadata.transcript_model_used == third_model
        # keep_forever stays True from the earlier merge
        assert twice_merged.metadata.keep_forever is True
        assert not fake_fs.directory_exists(third_bundle_dir)

    def test_handle_merge_concatenates_different_transcript_models(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Verify different transcript models from both bundles are both present after merge."""
        previous_model = "whisper"
        current_model = "gpt-4o-transcribe"
        previous_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_previous",
            audio_filename="Recording 20250115010000.mp3",
            transcript_text="previous transcript",
            audio_length=30.0,
            transcript_model_used=previous_model,
            keep_forever=False,
        )
        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_meeting",
            audio_filename="Recording 20250115090000.mp3",
            transcript_text="current transcript",
            audio_length=20.0,
            transcript_model_used=current_model,
            keep_forever=False,
        )

        previous_bundle_dir = previous_bundle.get_bundle_dir()

        with pytest.raises(AbortRemainingBundleJobsException):
            handle_merge(current_bundle, fake_config, "merge")

        merged_bundle = TranscribeBundle.from_existing_directory(
            existing_dir=previous_bundle_dir,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )

        # Both models should be present in the merged metadata, regardless of exact syntax.
        merged_model = merged_bundle.metadata.transcript_model_used
        assert merged_model is not None
        assert previous_model in merged_model
        assert current_model in merged_model

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

        with pytest.raises(NoPreviousBundleException):
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

        with pytest.raises(NoPreviousBundleException):
            handle_merge(current_bundle, fake_config, "merge")
