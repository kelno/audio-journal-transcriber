"""Tests for TranscribeBundle with FakeFileSystemService."""

from pathlib import Path

import pytest

from transcriber.commands.command_type import CommandType
from transcriber.config import TranscribeConfig
from transcriber.constants import (
    COMMANDS_FILENAME,
    METADATA_FILENAME,
    SUMMARY_FILENAME,
    TRANSCRIPT_FILENAME,
)
from transcriber.files.commands_file import CommandsFile
from transcriber.files.metadata import MetadataFile
from transcriber.files.text_file import SummaryFile, TranscriptFile
from transcriber.transcribe_bundle import TranscribeBundle

from .fake_file_system import FakeFileSystemService


@pytest.fixture
def generic_bundle_dir(
    fake_fs: FakeFileSystemService,
    fake_config: TranscribeConfig,
) -> Path:
    """Get a generic bundle directory, creating it in filesystem."""
    bundle_name = "2025-01-15_meeting"
    bundle_dir = fake_config.general.store_dir / bundle_name
    fake_fs.create_directory(bundle_dir)
    return bundle_dir


@pytest.fixture
def generic_bundle(
    fake_config: TranscribeConfig,
    fake_fs: FakeFileSystemService,
    generic_bundle_dir: Path,
) -> TranscribeBundle:
    """Create a generic bundle, containing only the audio & metadata.

    Will create directory and files in dir.
    """
    audio_filename = "meeting.mp3"
    bundle = TranscribeBundle(
        bundle_name=generic_bundle_dir.name,
        metadata=MetadataFile(original_audio_filenames=[audio_filename]),
        source_audios=[generic_bundle_dir / audio_filename],
        transcript=None,
        summary=None,
        commands=None,
        fs_service=fake_fs,
        config=fake_config,
    )

    bundle.init_metadata([audio_filename], [3600.0])  # create valid metadata + write file
    fake_fs.write_file(generic_bundle_dir / audio_filename, "Audio")
    return bundle


class TestTranscribeBundleFromAudioFile:
    """Tests for creating bundles from a single audio file."""

    def test_from_audio_file_creates_bundle(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Test creating a bundle from a single audio file."""
        audio_path = Path("/input/meeting.mp3")

        bundle = TranscribeBundle.from_audio_file(
            source_audio=audio_path,
            config=fake_config,
            fs_service=fake_fs,
        )

        # generate_generic_bundle_name should match this
        assert bundle.bundle_name.endswith("_meeting")
        assert bundle.source_audios == [audio_path]
        assert bundle.metadata.original_audio_filenames == ["meeting.mp3"]
        assert bundle.transcript is None
        assert bundle.summary is None

    def test_from_audio_file_uses_provided_fs_service(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Test that the provided fs_service is assigned to the bundle."""
        audio_path = Path("/input/test.mp3")

        bundle = TranscribeBundle.from_audio_file(
            source_audio=audio_path,
            config=fake_config,
            fs_service=fake_fs,
        )

        assert bundle.fs_service is fake_fs


class TestTranscribeBundleFromAudioFiles:
    """Tests for creating bundles from multiple audio files."""

    def test_from_audio_files_with_explicit_fs_service(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Test creating bundle from multiple files with explicit fs_service."""
        audio_files = [
            Path("/input/part1.mp3"),
            Path("/input/part2.mp3"),
        ]

        bundle = TranscribeBundle.from_audio_files(
            source_audios=audio_files,
            config=fake_config,
            fs_service=fake_fs,
        )

        assert bundle.source_audios == audio_files
        assert bundle.metadata.original_audio_filenames == ["part1.mp3", "part2.mp3"]

    def test_from_audio_files_without_fs_service_creates_real_service(
        self,
        fake_config: TranscribeConfig,
    ) -> None:
        """Test that from_audio_files creates RealFileSystemService if not provided."""
        audio_files = [Path("/input/test.mp3")]

        bundle = TranscribeBundle.from_audio_files(
            source_audios=audio_files,
            config=fake_config,
        )

        # Should have created a real service, not None
        assert bundle.fs_service is not None

    def test_from_audio_files_raises_on_empty_list(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Test that from_audio_files raises ValueError on empty list."""
        with pytest.raises(ValueError, match="Must provide at least one audio file"):
            TranscribeBundle.from_audio_files(
                source_audios=[],
                config=fake_config,
                fs_service=fake_fs,
            )


class TestTranscribeBundleFromExistingDirectory:
    """Tests for loading bundles from existing directories."""

    def test_from_existing_directory_loads_bundle(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Test loading a bundle from an existing directory."""
        bundle_dir = Path("/store/2025-01-15_meeting")
        metadata_yaml = (
            "---\n"
            "original_audio_filenames: [meeting.mp3]\n"
            "audio_length: 3600.0\n"
            "transcript_model_used: null\n"
            "summary_model_used: null\n"
            "bundle_name_generated: false\n"
            "keep_forever: false\n"
            "---\n"
        )
        transcript_content = "This is the transcript."

        # Set up fake file system
        fake_fs.create_directory(bundle_dir)
        fake_fs.write_file(bundle_dir / METADATA_FILENAME, metadata_yaml)
        fake_fs.write_file(bundle_dir / "meeting.mp3", b"fake audio content".decode())
        fake_fs.write_file(bundle_dir / TRANSCRIPT_FILENAME, transcript_content)

        # Load bundle
        bundle = TranscribeBundle.from_existing_directory(
            existing_dir=bundle_dir,
            config=fake_config,
            fs_service=fake_fs,
        )
        bundle.cleanup_inconsistencies(False)

        assert bundle.bundle_name == "2025-01-15_meeting"
        assert bundle.source_audios == [bundle_dir / "meeting.mp3"]
        assert bundle.metadata.audio_length == 3600.0
        assert bundle.transcript is not None
        assert isinstance(bundle.transcript, TranscriptFile)

    def test_from_existing_directory_raises_on_missing_metadata(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Test that loading raises if metadata file is missing."""
        bundle_dir = Path("/store/2025-01-15_meeting")
        fake_fs.create_directory(bundle_dir)
        fake_fs.write_file(bundle_dir / "meeting.mp3", "fake audio")

        with pytest.raises(
            ValueError,
            match=r"Bundle directory is invalid.*no meta file",
        ):
            TranscribeBundle.from_existing_directory(
                existing_dir=bundle_dir,
                config=fake_config,
                fs_service=fake_fs,
            )


class TestTranscribeBundleWriteOperations:
    """Tests for writing bundle data (transcript, summary, metadata)."""

    def test_set_and_write_transcript(
        self,
        fake_fs: FakeFileSystemService,
        generic_bundle_dir: Path,
        generic_bundle: TranscribeBundle,
    ) -> None:
        """Test setting and writing a transcript."""
        transcript_text = "This is the meeting transcript."
        generic_bundle.set_and_write_transcript(transcript_text)

        # Check transcript was set
        assert generic_bundle.transcript is not None
        assert generic_bundle.transcript.text == transcript_text

        # Check file was written
        assert fake_fs.file_exists(generic_bundle_dir / TRANSCRIPT_FILENAME)
        assert fake_fs.read_file(generic_bundle_dir / TRANSCRIPT_FILENAME) == transcript_text

        # Check metadata was updated
        assert fake_fs.file_exists(generic_bundle_dir / METADATA_FILENAME)

    def test_set_and_write_summary(
        self,
        fake_fs: FakeFileSystemService,
        generic_bundle_dir: Path,
        generic_bundle: TranscribeBundle,
    ) -> None:
        """Test setting and writing a summary."""
        summary_text = "Key points from the meeting."
        generic_bundle.set_and_write_summary(summary_text)

        # Check summary was set
        assert generic_bundle.summary is not None
        assert generic_bundle.summary.text == summary_text

        # Check file was written
        assert fake_fs.file_exists(generic_bundle_dir / SUMMARY_FILENAME)
        assert fake_fs.read_file(generic_bundle_dir / SUMMARY_FILENAME) == summary_text

    def test_set_and_write_commands(
        self,
        generic_bundle_dir: Path,
        generic_bundle: TranscribeBundle,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Test setting and writing commands."""
        command_one = "do the thing"
        command_two = "do the other thing"
        generic_bundle.set_and_write_commands([command_one, command_two])

        # Check commands were set
        assert generic_bundle.commands is not None
        assert command_one in [item.text for item in generic_bundle.commands.commands]
        assert command_two in [item.text for item in generic_bundle.commands.commands]

        # Check file was written
        assert fake_fs.file_exists(generic_bundle_dir / COMMANDS_FILENAME)

    def test_set_command_type_and_executed(
        self,
        generic_bundle: TranscribeBundle,
    ) -> None:
        """Test set_command_type and set_command_executed."""
        command_one = "do the thing"
        command_two = "do the other thing"
        generic_bundle.set_and_write_commands([command_one, command_two])
        generic_bundle.set_command_type(command_one, CommandType.MERGE)

        assert generic_bundle.commands
        assert generic_bundle.commands.commands[0].matched_type == CommandType.MERGE
        assert generic_bundle.commands.commands[1].matched_type is None
        assert not generic_bundle.commands.commands[0].executed
        assert not generic_bundle.commands.commands[1].executed
        assert generic_bundle.commands.commands[0].executed_at is None
        assert generic_bundle.commands.commands[1].executed_at is None

        generic_bundle.set_command_executed(command_one)
        assert generic_bundle.commands.commands[0].executed
        assert not generic_bundle.commands.commands[1].executed
        assert generic_bundle.commands.commands[0].executed_at is not None
        assert generic_bundle.commands.commands[1].executed_at is None

    def test_init_metadata(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        generic_bundle_dir: Path,
    ) -> None:
        """Test initializing metadata with file information."""
        bundle = TranscribeBundle(
            bundle_name=generic_bundle_dir.name,
            metadata=MetadataFile(original_audio_filenames=[]),
            source_audios=[],
            transcript=None,
            summary=None,
            commands=None,
            fs_service=fake_fs,
            config=fake_config,
        )

        filenames = ["part1.mp3", "part2.mp3"]
        audio_lengths = [1800.0, 1200.0]

        bundle.init_metadata(filenames, audio_lengths)

        assert bundle.metadata.original_audio_filenames == filenames
        assert bundle.metadata.audio_length == 3000.0
        assert fake_fs.file_exists(generic_bundle_dir / METADATA_FILENAME)


class TestTranscribeBundleCleanupInconsistencies:
    """Tests cleanup_inconsistencies."""

    def test_cleanup_inconsistencies_removes_inconsistent_files(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        generic_bundle_dir: Path,
    ) -> None:
        """Test that cleanup_inconsistencies removes inconsistent files."""
        bundle = TranscribeBundle(
            fs_service=fake_fs,
            config=fake_config,
            bundle_name=generic_bundle_dir.name,
            metadata=MetadataFile(original_audio_filenames=["meeting.mp3"]),
            source_audios=[generic_bundle_dir / "another_audio_file.mp3"],
            transcript=TranscriptFile("test transcript"),
            summary=SummaryFile("test summary"),
            commands=CommandsFile(""),
        )
        bundle.source_audios = [generic_bundle_dir / "another_audio_file.mp3"]

        fake_fs.write_file(generic_bundle_dir / METADATA_FILENAME, "Old metadata")
        fake_fs.write_file(generic_bundle_dir / "another_audio_file.mp3", "Old audio")
        fake_fs.write_file(generic_bundle_dir / TRANSCRIPT_FILENAME, "Old transcript")
        fake_fs.write_file(generic_bundle_dir / SUMMARY_FILENAME, "Old summary")
        fake_fs.write_file(generic_bundle_dir / COMMANDS_FILENAME, "Old commands")

        bundle.cleanup_inconsistencies(dry_run=False)

        # the audio file should be kept
        assert fake_fs.file_exists(generic_bundle_dir / "another_audio_file.mp3")
        # but other files deleted
        assert not fake_fs.file_exists(generic_bundle_dir / TRANSCRIPT_FILENAME)
        assert not fake_fs.file_exists(generic_bundle_dir / SUMMARY_FILENAME)
        assert not fake_fs.file_exists(generic_bundle_dir / COMMANDS_FILENAME)


class TestTranscribeBundleRefresh:
    """Tests for refresh operation."""

    def test_refresh_clears_transcript_and_summary(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        generic_bundle_dir: Path,
    ) -> None:
        """Test that refresh clears transcript and summary."""
        bundle = TranscribeBundle(
            bundle_name=generic_bundle_dir.name,
            metadata=MetadataFile(original_audio_filenames=["meeting.mp3"]),
            source_audios=[generic_bundle_dir / "meeting.mp3"],
            transcript=TranscriptFile("Old transcript"),
            summary=SummaryFile("Old summary"),
            commands=CommandsFile(""),
            fs_service=fake_fs,
            config=fake_config,
        )

        # Write files first
        fake_fs.write_file(generic_bundle_dir / METADATA_FILENAME, "Old metadata")
        fake_fs.write_file(generic_bundle_dir / "meeting.mp3", "Old audio")
        fake_fs.write_file(generic_bundle_dir / TRANSCRIPT_FILENAME, "Old transcript")
        fake_fs.write_file(generic_bundle_dir / SUMMARY_FILENAME, "Old summary")
        fake_fs.write_file(generic_bundle_dir / COMMANDS_FILENAME, "Old commands")

        bundle.refresh(dry_run=False)

        # Check they were cleared from bundle
        assert bundle.transcript is None
        assert bundle.summary is None
        assert bundle.commands is None

        # Check metadata was updated (bundle_name_generated = False)
        assert bundle.metadata.bundle_name_generated is False

        # Check files were deleted
        assert not fake_fs.file_exists(generic_bundle_dir / TRANSCRIPT_FILENAME)
        assert not fake_fs.file_exists(generic_bundle_dir / SUMMARY_FILENAME)
        assert not fake_fs.file_exists(generic_bundle_dir / COMMANDS_FILENAME)

    def test_refresh_dry_run_does_not_delete_files(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        generic_bundle_dir: Path,
    ) -> None:
        """Test that refresh with dry_run=True does not delete files."""
        bundle = TranscribeBundle(
            bundle_name=generic_bundle_dir.name,
            metadata=MetadataFile(original_audio_filenames=["meeting.mp3"]),
            source_audios=[generic_bundle_dir / "meeting.mp3"],
            transcript=TranscriptFile("Transcript"),
            summary=SummaryFile("Summary"),
            fs_service=fake_fs,
            config=fake_config,
        )

        # Write files first
        fake_fs.write_file(generic_bundle_dir / TRANSCRIPT_FILENAME, "Transcript")
        fake_fs.write_file(generic_bundle_dir / SUMMARY_FILENAME, "Summary")
        fake_fs.write_file(generic_bundle_dir / COMMANDS_FILENAME, "Commands")

        bundle.refresh(dry_run=True)

        # Files should still exist
        assert fake_fs.file_exists(generic_bundle_dir / TRANSCRIPT_FILENAME)
        assert fake_fs.file_exists(generic_bundle_dir / SUMMARY_FILENAME)
        assert fake_fs.file_exists(generic_bundle_dir / COMMANDS_FILENAME)

        # But transcript and summary should be cleared from bundle
        assert bundle.transcript is None
        assert bundle.summary is None
        assert bundle.commands is None


class TestTranscribeBundleRenaming:
    """Tests for bundle directory renaming."""

    def test_set_and_write_bundle_name(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        generic_bundle_dir: Path,
        generic_bundle: TranscribeBundle,
    ) -> None:
        """Test setting and writing a new bundle name (directory rename)."""
        store_dir = fake_config.general.store_dir

        old_path = generic_bundle_dir
        new_name_summary = "Q4 Planning Session"
        new_path = store_dir / "2025-01-15 Q4 Planning Session"

        # Rename the bundle
        generic_bundle.set_and_write_bundle_name(new_name_summary)

        # Check bundle name was updated with correct prefix
        assert generic_bundle.bundle_name.endswith(new_name_summary)

        # Check old directory no longer exists
        assert not fake_fs.directory_exists(old_path)

        # Check new directory exists with files
        new_path = store_dir / generic_bundle.bundle_name
        assert fake_fs.directory_exists(new_path)
        assert fake_fs.file_exists(new_path / "meeting.mp3")
        assert fake_fs.file_exists(new_path / METADATA_FILENAME)

        # Check metadata was marked as generated
        assert generic_bundle.metadata.bundle_name_generated is True

    def test_set_and_write_bundle_name_raises_on_missing_directory(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Test that renaming raises if directory doesn't exist."""
        bundle = TranscribeBundle(
            bundle_name="2025-01-15_meeting",
            metadata=MetadataFile(original_audio_filenames=["meeting.mp3"]),
            source_audios=[Path("/store/2025-01-15_meeting/meeting.mp3")],
            fs_service=fake_fs,
            config=fake_config,
        )
        # don't write it to filesystem

        with pytest.raises(FileNotFoundError, match=r"Bundle directory.*not found"):
            bundle.set_and_write_bundle_name("New Name")


class TestGatherExistingBundles:
    """Tests for gathering bundles from a directory."""

    def test_gather_existing_bundles_finds_valid_bundles(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Test that gather_existing_bundles finds valid bundles."""
        store_dir = fake_config.general.store_dir
        bundle1_dir = store_dir / "2025-01-15_meeting1"
        bundle2_dir = store_dir / "2025-01-15_meeting2"

        metadata_yaml = (
            "---\n"
            "original_audio_filenames: [meeting.mp3]\n"
            "audio_length: null\n"
            "transcript_model_used: null\n"
            "summary_model_used: null\n"
            "bundle_name_generated: false\n"
            "keep_forever: false\n"
            "---\n"
        )

        # Create first bundle
        fake_fs.create_directory(bundle1_dir)
        fake_fs.write_file(bundle1_dir / METADATA_FILENAME, metadata_yaml)
        fake_fs.write_file(bundle1_dir / "meeting.mp3", "audio1")

        # Create second bundle
        fake_fs.create_directory(bundle2_dir)
        fake_fs.write_file(bundle2_dir / METADATA_FILENAME, metadata_yaml)
        fake_fs.write_file(bundle2_dir / "meeting.mp3", "audio2")

        bundles = TranscribeBundle.gather_existing_bundles(
            output_dir=store_dir,
            dry_run=False,
            config=fake_config,
            fs_service=fake_fs,
        )

        assert len(bundles) == 2
        assert {b.bundle_name for b in bundles} == {
            "2025-01-15_meeting1",
            "2025-01-15_meeting2",
        }

    def test_gather_existing_bundles_skips_invalid_bundles(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        generic_bundle: TranscribeBundle,
    ) -> None:
        """Test that gather_existing_bundles skips invalid bundles."""
        store_dir = fake_config.general.store_dir
        invalid_bundle_dir = store_dir / "2025-01-15_invalid"

        # Create invalid bundle (missing metadata)
        fake_fs.create_directory(invalid_bundle_dir)
        fake_fs.write_file(invalid_bundle_dir / "meeting.mp3", "audio")

        bundles = TranscribeBundle.gather_existing_bundles(
            output_dir=store_dir,
            dry_run=False,
            config=fake_config,
            fs_service=fake_fs,
        )

        # at this point we have 1 valid bundle (generic_bundle) and 1 invalid one
        assert len(bundles) == 1
        assert bundles[0].bundle_name == generic_bundle.bundle_name


class TestTranscribeBundleIntegration:
    """Integration tests for complete workflows."""

    def test_complete_workflow_create_load_update(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Test a complete workflow: create, load, update bundle."""
        # Step 1: Create bundle from audio file
        audio_path = Path("/input/meeting.mp3")
        bundle = TranscribeBundle.from_audio_file(
            source_audio=audio_path,
            fs_service=fake_fs,
            config=fake_config,
        )
        assert bundle.transcript is None
        assert bundle.summary is None

        # Step 2: Set up directory
        bundle_dir = fake_config.general.store_dir / bundle.bundle_name
        fake_fs.create_directory(bundle_dir)
        fake_fs.write_file(bundle_dir / "meeting.mp3", "fake audio")

        # Step 3: Write transcript
        bundle.set_and_write_transcript("Meeting transcript here.")
        assert bundle.transcript is not None

        # Step 4: Write summary
        bundle.set_and_write_summary("Summary of meeting.")
        assert bundle.summary is not None

        # Step 5: Write commands
        command_one = "do the thing"
        command_two = "do the other thing"
        bundle.set_and_write_commands([command_one, command_two])
        assert bundle.commands is not None

        # Step 6: Reload bundle from disk
        reloaded = TranscribeBundle.from_existing_directory(
            existing_dir=bundle_dir,
            config=fake_config,
            fs_service=fake_fs,
        )
        assert reloaded.transcript is not None
        assert reloaded.transcript.text == "Meeting transcript here."
        assert reloaded.summary is not None
        assert reloaded.summary.text == "Summary of meeting."
        assert reloaded.commands is not None
        assert bundle.commands is not None
        assert command_one in [item.text for item in bundle.commands.commands]
        assert command_two in [item.text for item in bundle.commands.commands]

    def test_workflow_with_new_audio_files_detected(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Test that refresh is triggered when new audio files are detected."""
        bundle_dir = Path("/store/2025-01-15_meeting")
        fake_fs.create_directory(bundle_dir)

        # Create initial metadata with one file
        metadata_yaml = (
            "---\n"
            "original_audio_filenames: [part1.mp3]\n"
            "audio_length: null\n"
            "transcript_model_used: null\n"
            "summary_model_used: null\n"
            "bundle_name_generated: false\n"
            "keep_forever: false\n"
            "---\n"
        )
        fake_fs.write_file(bundle_dir / METADATA_FILENAME, metadata_yaml)
        fake_fs.write_file(bundle_dir / "part1.mp3", "audio1")
        fake_fs.write_file(bundle_dir / "part2.mp3", "audio2")

        # Load bundle - should detect new file and trigger refresh
        bundle = TranscribeBundle.from_existing_directory(
            existing_dir=bundle_dir,
            config=fake_config,
            fs_service=fake_fs,
        )

        # Bundle should have both files now
        assert len(bundle.source_audios) == 2
        assert {f.name for f in bundle.source_audios} == {"part1.mp3", "part2.mp3"}
