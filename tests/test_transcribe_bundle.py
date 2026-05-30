"""Tests for TranscribeBundle with FakeFileSystemService."""

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from transcriber.config import AudioConfig, GeneralConfig, TextConfig, TranscribeConfig
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
def fake_config() -> TranscribeConfig:
    """Create a minimal config for testing."""
    return TranscribeConfig(
        general=GeneralConfig(
            input_dir=Path("/fake/input"),
            store_dir=Path("/fake/store"),
            delete_source_audio_after_days=0,
            min_length_seconds=0.0,
            remove_short_files=False,
            timezone=ZoneInfo("UTC"),
        ),
        text=TextConfig(
            summary_enabled=True,
            api_base_url="https://api.example.com",
            model="gpt-4",
            api_key="test-key",
            extra_context=None,
        ),
        audio=AudioConfig(
            api_base_url="https://api.example.com",
            model="whisper-1",
            api_key="test-key",
            stream=False,
        ),
    )


@pytest.fixture
def fake_fs() -> FakeFileSystemService:
    """Create a fresh fake file system for each test."""
    return FakeFileSystemService()


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
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Test setting and writing a transcript."""
        bundle_dir = fake_config.general.store_dir / "2025-01-15_meeting"
        fake_fs.create_directory(bundle_dir)

        bundle = TranscribeBundle(
            bundle_name="2025-01-15_meeting",
            metadata=MetadataFile(original_audio_filenames=["meeting.mp3"]),
            source_audios=[bundle_dir / "meeting.mp3"],
            transcript=None,
            summary=None,
            commands=None,
            fs_service=fake_fs,
            config=fake_config,
        )

        transcript_text = "This is the meeting transcript."
        bundle.set_and_write_transcript(transcript_text)

        # Check transcript was set
        assert bundle.transcript is not None
        assert bundle.transcript.text == transcript_text

        # Check file was written
        assert fake_fs.file_exists(bundle_dir / TRANSCRIPT_FILENAME)
        assert fake_fs.read_file(bundle_dir / TRANSCRIPT_FILENAME) == transcript_text

        # Check metadata was updated
        assert fake_fs.file_exists(bundle_dir / METADATA_FILENAME)

    def test_set_and_write_summary(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Test setting and writing a summary."""
        bundle_dir = fake_config.general.store_dir / "2025-01-15_meeting"
        fake_fs.create_directory(bundle_dir)

        bundle = TranscribeBundle(
            bundle_name="2025-01-15_meeting",
            metadata=MetadataFile(original_audio_filenames=["meeting.mp3"]),
            source_audios=[bundle_dir / "meeting.mp3"],
            transcript=None,
            summary=None,
            commands=None,
            fs_service=fake_fs,
            config=fake_config,
        )

        summary_text = "Key points from the meeting."
        bundle.set_and_write_summary(summary_text)

        # Check summary was set
        assert bundle.summary is not None
        assert bundle.summary.text == summary_text

        # Check file was written
        assert fake_fs.file_exists(bundle_dir / SUMMARY_FILENAME)
        assert fake_fs.read_file(bundle_dir / SUMMARY_FILENAME) == summary_text

    def test_set_and_write_commands(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Test setting and writing commands."""
        bundle_dir = fake_config.general.store_dir / "2025-01-15_meeting"
        fake_fs.create_directory(bundle_dir)

        bundle = TranscribeBundle(
            bundle_name="2025-01-15_meeting",
            metadata=MetadataFile(original_audio_filenames=["meeting.mp3"]),
            source_audios=[bundle_dir / "meeting.mp3"],
            transcript=None,
            summary=None,
            commands=None,
            fs_service=fake_fs,
            config=fake_config,
        )

        one_command = "do the thing"
        two_command = "do the other thing"
        bundle.set_and_write_commands([one_command, two_command])

        # Check summary was set
        assert bundle.commands is not None
        assert one_command in [item.text for item in bundle.commands.commands]
        assert two_command in [item.text for item in bundle.commands.commands]

        # Check file was written
        assert fake_fs.file_exists(bundle_dir / COMMANDS_FILENAME)

    def test_init_metadata(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Test initializing metadata with file information."""
        bundle_dir = fake_config.general.store_dir / "2025-01-15_meeting"
        fake_fs.create_directory(bundle_dir)

        bundle = TranscribeBundle(
            bundle_name="2025-01-15_meeting",
            metadata=MetadataFile(original_audio_filenames=[]),
            source_audios=[],
            transcript=None,
            summary=None,
            fs_service=fake_fs,
            config=fake_config,
        )

        filenames = ["part1.mp3", "part2.mp3"]
        audio_lengths = [1800.0, 1200.0]

        bundle.init_metadata(filenames, audio_lengths)

        assert bundle.metadata.original_audio_filenames == filenames
        assert bundle.metadata.audio_length == 3000.0
        assert fake_fs.file_exists(bundle_dir / METADATA_FILENAME)


class TestTranscribeBundleCleanupInconsistencies:
    """Tests cleanup_inconsistencies."""

    def test_cleanup_inconsistencies_removes_inconsistent_files(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Test that cleanup_inconsistencies removes inconsistent files."""
        bundle_dir = fake_config.general.store_dir / "2025-01-15_meeting"
        fake_fs.create_directory(bundle_dir)

        bundle = TranscribeBundle(
            fs_service=fake_fs,
            config=fake_config,
            bundle_name="2025-01-15_meeting",
            metadata=MetadataFile(original_audio_filenames=["meeting.mp3"]),
            source_audios=[bundle_dir / "another_audio_file.mp3"],
            transcript=TranscriptFile("test transcript"),
            summary=SummaryFile("test summary"),
            commands=CommandsFile(""),
        )

        fake_fs.write_file(bundle_dir / METADATA_FILENAME, "Old metadata")
        fake_fs.write_file(bundle_dir / "another_audio_file.mp3", "Old audio")
        fake_fs.write_file(bundle_dir / TRANSCRIPT_FILENAME, "Old transcript")
        fake_fs.write_file(bundle_dir / SUMMARY_FILENAME, "Old summary")
        fake_fs.write_file(bundle_dir / COMMANDS_FILENAME, "Old commands")

        bundle.cleanup_inconsistencies(dry_run=False)

        # the audio file should be kept
        assert fake_fs.file_exists(bundle_dir / "another_audio_file.mp3")
        # but other files deleted
        assert not fake_fs.file_exists(bundle_dir / TRANSCRIPT_FILENAME)
        assert not fake_fs.file_exists(bundle_dir / SUMMARY_FILENAME)
        assert not fake_fs.file_exists(bundle_dir / COMMANDS_FILENAME)


class TestTranscribeBundleRefresh:
    """Tests for refresh operation."""

    def test_refresh_clears_transcript_and_summary(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Test that refresh clears transcript and summary."""
        bundle_dir = fake_config.general.store_dir / "2025-01-15_meeting"
        fake_fs.create_directory(bundle_dir)

        bundle = TranscribeBundle(
            bundle_name="2025-01-15_meeting",
            metadata=MetadataFile(original_audio_filenames=["meeting.mp3"]),
            source_audios=[bundle_dir / "meeting.mp3"],
            transcript=TranscriptFile("Old transcript"),
            summary=SummaryFile("Old summary"),
            commands=CommandsFile(""),
            fs_service=fake_fs,
            config=fake_config,
        )

        # Write files first
        fake_fs.write_file(bundle_dir / METADATA_FILENAME, "Old metadata")
        fake_fs.write_file(bundle_dir / "meeting.mp3", "Old audio")
        fake_fs.write_file(bundle_dir / TRANSCRIPT_FILENAME, "Old transcript")
        fake_fs.write_file(bundle_dir / SUMMARY_FILENAME, "Old summary")
        fake_fs.write_file(bundle_dir / COMMANDS_FILENAME, "Old commands")

        bundle.refresh(dry_run=False)

        # Check they were cleared from bundle
        assert bundle.transcript is None
        assert bundle.summary is None
        assert bundle.commands is None

        # Check metadata was updated (bundle_name_generated = False)
        assert bundle.metadata.bundle_name_generated is False

        # Check files were deleted
        assert not fake_fs.file_exists(bundle_dir / TRANSCRIPT_FILENAME)
        assert not fake_fs.file_exists(bundle_dir / SUMMARY_FILENAME)
        assert not fake_fs.file_exists(bundle_dir / COMMANDS_FILENAME)

    def test_refresh_dry_run_does_not_delete_files(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Test that refresh with dry_run=True does not delete files."""
        bundle_dir = Path("/store/2025-01-15_meeting")
        fake_fs.create_directory(bundle_dir)

        bundle = TranscribeBundle(
            bundle_name="2025-01-15_meeting",
            metadata=MetadataFile(original_audio_filenames=["meeting.mp3"]),
            source_audios=[bundle_dir / "meeting.mp3"],
            transcript=TranscriptFile("Transcript"),
            summary=SummaryFile("Summary"),
            fs_service=fake_fs,
            config=fake_config,
        )

        # Write files first
        fake_fs.write_file(bundle_dir / TRANSCRIPT_FILENAME, "Transcript")
        fake_fs.write_file(bundle_dir / SUMMARY_FILENAME, "Summary")
        fake_fs.write_file(bundle_dir / COMMANDS_FILENAME, "Commands")

        bundle.refresh(dry_run=True)

        # Files should still exist
        assert fake_fs.file_exists(bundle_dir / TRANSCRIPT_FILENAME)
        assert fake_fs.file_exists(bundle_dir / SUMMARY_FILENAME)
        assert fake_fs.file_exists(bundle_dir / COMMANDS_FILENAME)

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
    ) -> None:
        """Test setting and writing a new bundle name (directory rename)."""
        store_dir = fake_config.general.store_dir
        old_name = "2025-01-15_meeting"
        new_name_summary = "Q4 Planning Session"
        old_path = store_dir / old_name
        new_path = store_dir / "2025-01-15 Q4 Planning Session"

        # Set up old directory
        fake_fs.create_directory(old_path)
        fake_fs.write_file(old_path / "meeting.mp3", "fake audio")
        fake_fs.write_file(
            old_path / METADATA_FILENAME,
            "---\noriginal_audio_filenames: [meeting.mp3]\n---\n",
        )

        bundle = TranscribeBundle(
            bundle_name=old_name,
            metadata=MetadataFile(original_audio_filenames=["meeting.mp3"]),
            source_audios=[old_path / "meeting.mp3"],
            transcript=None,
            summary=None,
            fs_service=fake_fs,
            config=fake_config,
        )

        # Rename the bundle
        bundle.set_and_write_bundle_name(new_name_summary)

        # Check bundle name was updated with correct prefix
        assert bundle.bundle_name.endswith(new_name_summary)

        # Check old directory no longer exists
        assert not fake_fs.directory_exists(old_path)

        # Check new directory exists with files
        new_path = store_dir / bundle.bundle_name
        assert fake_fs.directory_exists(new_path)
        assert fake_fs.file_exists(new_path / "meeting.mp3")
        assert fake_fs.file_exists(new_path / METADATA_FILENAME)

        # Check metadata was marked as generated
        assert bundle.metadata.bundle_name_generated is True

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
            transcript=None,
            summary=None,
            fs_service=fake_fs,
            config=fake_config,
        )

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
    ) -> None:
        """Test that gather_existing_bundles skips invalid bundles."""
        store_dir = fake_config.general.store_dir
        valid_bundle_dir = store_dir / "2025-01-15_valid"
        invalid_bundle_dir = store_dir / "2025-01-15_invalid"

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

        # Create valid bundle
        fake_fs.create_directory(valid_bundle_dir)
        fake_fs.write_file(valid_bundle_dir / METADATA_FILENAME, metadata_yaml)
        fake_fs.write_file(valid_bundle_dir / "meeting.mp3", "audio")

        # Create invalid bundle (missing metadata)
        fake_fs.create_directory(invalid_bundle_dir)
        fake_fs.write_file(invalid_bundle_dir / "meeting.mp3", "audio")

        bundles = TranscribeBundle.gather_existing_bundles(
            output_dir=store_dir,
            dry_run=False,
            config=fake_config,
            fs_service=fake_fs,
        )

        assert len(bundles) == 1
        assert bundles[0].bundle_name == "2025-01-15_valid"


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

        # Step 5: Reload bundle from disk
        reloaded = TranscribeBundle.from_existing_directory(
            existing_dir=bundle_dir,
            config=fake_config,
            fs_service=fake_fs,
        )
        assert reloaded.transcript is not None
        assert reloaded.transcript.text == "Meeting transcript here."
        assert reloaded.summary is not None
        assert reloaded.summary.text == "Summary of meeting."

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
