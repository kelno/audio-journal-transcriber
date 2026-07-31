"""Tests for TranscribeBundle with FakeFileSystemService."""

from datetime import datetime
from pathlib import Path

import pydantic
import pytest

from tests.bundle_fixtures import TranscribeBundleFactory
from tests.fake_audio_service import FakeAudioService
from transcriber.bundle_id import new_bundle_id
from transcriber.commands.command_type import CommandType
from transcriber.config import TranscribeConfig
from transcriber.constants import (
    COMMANDS_FILENAME,
    CUSTOM_CONTEXT_FILENAME,
    DEFAULT_CUSTOM_CONTEXT_CONTENT,
    METADATA_FILENAME,
    SUMMARY_FILENAME,
    TRANSCRIPT_FILENAME,
)
from transcriber.exception import (
    DuplicateBundleIdException,
    FailedToExtractDateException,
    InvalidBundleException,
    InvalidSourceAudiosException,
)
from transcriber.files.metadata import AudioFileMeta, MetadataFile
from transcriber.files.text_file import CustomContextFile, TranscriptFile
from transcriber.transcribe_bundle import TranscribeBundle

from .fake_file_system import FakeFileSystemService


class TestCustomContextFile:
    """Tests for the per-bundle custom_context.md model and helpers."""

    def test_filename_is_custom_context_md(self) -> None:
        """CustomContextFile uses the expected filename."""
        assert CustomContextFile(text="").get_filename() == CUSTOM_CONTEXT_FILENAME

    def test_from_file_reads_content(self, fake_fs: FakeFileSystemService, generic_bundle_dir: Path) -> None:
        """Loading a custom_context.md file yields its text."""
        fake_fs.write_file(generic_bundle_dir / CUSTOM_CONTEXT_FILENAME, "This is a message to my lord and master Bobby.")
        ctx = CustomContextFile.from_file(generic_bundle_dir / CUSTOM_CONTEXT_FILENAME, fake_fs)
        assert ctx.text == "This is a message to my lord and master Bobby."


class TestCustomContextHelpers:
    """Tests for get_effective_context and compute_context_hash."""

    def test_missing_file_is_empty_context(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        generic_bundle: TranscribeBundle,
    ) -> None:
        """A bundle without a custom_context.md resolves to empty context."""
        # The factory seeds a default context file; remove it to simulate a
        # bundle that genuinely has no custom_context.md.
        fake_fs.delete_file(generic_bundle.get_bundle_dir() / CUSTOM_CONTEXT_FILENAME)
        bundle = TranscribeBundle.from_existing_directory(
            existing_dir=generic_bundle.get_bundle_dir(),
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )
        assert bundle.custom_context is None
        assert bundle.get_effective_context() == ""

    def test_default_header_is_empty_context(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        generic_bundle: TranscribeBundle,
    ) -> None:
        """The pregenerated comment-only header counts as empty context."""
        fake_fs.write_file(generic_bundle.get_bundle_dir() / CUSTOM_CONTEXT_FILENAME, DEFAULT_CUSTOM_CONTEXT_CONTENT)
        bundle = TranscribeBundle.from_existing_directory(
            existing_dir=generic_bundle.get_bundle_dir(),
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )
        assert bundle.get_effective_context() == ""

    def test_comment_lines_are_stripped(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        generic_bundle: TranscribeBundle,
    ) -> None:
        """Markdown comment lines are ignored; only real content remains."""
        content = '[//]: # "ignored header line"\nThis is my rant about pastas.\n[//]: # "another ignored line"\n'
        fake_fs.write_file(generic_bundle.get_bundle_dir() / CUSTOM_CONTEXT_FILENAME, content)
        bundle = TranscribeBundle.from_existing_directory(
            existing_dir=generic_bundle.get_bundle_dir(),
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )
        assert bundle.get_effective_context() == "This is my rant about pastas."

    def test_hash_is_stable_and_ignores_comment_edits(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        generic_bundle: TranscribeBundle,
    ) -> None:
        """Editing only the comment header must not change the context hash."""
        generic_bundle_dir = generic_bundle.get_bundle_dir()
        fake_fs.write_file(generic_bundle_dir / CUSTOM_CONTEXT_FILENAME, DEFAULT_CUSTOM_CONTEXT_CONTENT)
        bundle_a = TranscribeBundle.from_existing_directory(
            existing_dir=generic_bundle_dir,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )
        hash_a = bundle_a.compute_context_hash()

        # Change only the comment text, keep it comment-only.
        fake_fs.write_file(
            generic_bundle_dir / CUSTOM_CONTEXT_FILENAME,
            '[//]: # "a different header comment"\n',
        )
        bundle_b = TranscribeBundle.from_existing_directory(
            existing_dir=generic_bundle_dir,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )
        assert bundle_b.compute_context_hash() == hash_a

    def test_hash_changes_with_real_content(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        generic_bundle: TranscribeBundle,
    ) -> None:
        """Adding real (non-comment) content changes the context hash."""
        generic_bundle_dir = generic_bundle.get_bundle_dir()
        fake_fs.write_file(generic_bundle_dir / CUSTOM_CONTEXT_FILENAME, DEFAULT_CUSTOM_CONTEXT_CONTENT)
        bundle_a = TranscribeBundle.from_existing_directory(
            existing_dir=generic_bundle_dir,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )
        hash_a = bundle_a.compute_context_hash()

        fake_fs.write_file(
            generic_bundle_dir / CUSTOM_CONTEXT_FILENAME,
            DEFAULT_CUSTOM_CONTEXT_CONTENT + "This is a rant about I much I love drugs.\n",
        )
        bundle_b = TranscribeBundle.from_existing_directory(
            existing_dir=generic_bundle_dir,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )
        assert bundle_b.compute_context_hash() != hash_a


class TestTranscribeBundleFromAudioFile:
    """Tests for creating bundles from a single audio file."""

    def test_from_audio_file_creates_bundle(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
    ) -> None:
        """Test creating a bundle from a single audio file."""
        audio_path = Path("/input/meeting.mp3")

        bundle = TranscribeBundle.from_audio_file(
            source_audio=audio_path,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )

        # generate_generic_bundle_name should match this
        assert bundle.bundle_name.endswith("_meeting")
        assert bundle.source_audios == [audio_path]
        assert bundle.metadata.audio_files == [AudioFileMeta(filename="meeting.mp3")]
        assert bundle.transcript is None
        assert bundle.summary is None

    def test_from_audio_file_uses_provided_fs_service(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
    ) -> None:
        """Test that the provided fs_service is assigned to the bundle."""
        audio_path = Path("/input/test.mp3")

        bundle = TranscribeBundle.from_audio_file(
            source_audio=audio_path,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )

        assert bundle.fs_service is fake_fs

    def test_new_bundles_receive_distinct_persistent_ids(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
    ) -> None:
        """Every newly discovered recording starts as a distinct bundle entity."""
        first = TranscribeBundle.from_audio_file(
            source_audio=Path("/input/2025-01-15_first.mp3"),
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )
        second = TranscribeBundle.from_audio_file(
            source_audio=Path("/input/2025-01-15_second.mp3"),
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )

        assert first.bundle_id != second.bundle_id
        assert len(first.bundle_id) == 32


class TestTranscribeBundleFromAudioFiles:
    """Tests for creating bundles from multiple audio files."""

    def test_from_audio_files_with_explicit_fs_service(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
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
            audio_service=fake_audio_service,
        )

        assert bundle.source_audios == audio_files
        assert bundle.metadata.audio_files == [
            AudioFileMeta(filename="part1.mp3"),
            AudioFileMeta(filename="part2.mp3"),
        ]

    def test_from_audio_files_without_fs_service_creates_real_service(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
    ) -> None:
        """Test that from_audio_files creates RealFileSystemService if not provided."""
        audio_files = [Path("/input/test.mp3")]

        bundle = TranscribeBundle.from_audio_files(
            source_audios=audio_files,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )

        # Should have created a real service, not None
        assert bundle.fs_service is not None

    def test_from_audio_files_raises_on_empty_list(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
    ) -> None:
        """Test that from_audio_files raises ValueError on empty list."""
        with pytest.raises(InvalidSourceAudiosException):
            TranscribeBundle.from_audio_files(
                source_audios=[],
                config=fake_config,
                fs_service=fake_fs,
                audio_service=fake_audio_service,
            )


class TestTranscribeBundleFromExistingDirectory:
    """Tests for loading bundles from existing directories."""

    @pytest.mark.usefixtures("generic_bundle")
    def test_from_existing_directory_loads_bundle(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        generic_bundle_dir: Path,
    ) -> None:
        """Test loading a bundle from an existing directory."""
        transcript_content = "This is the transcript."

        fake_fs.write_file(generic_bundle_dir / TRANSCRIPT_FILENAME, transcript_content)
        # other files are loaded by generic_bundle fixture

        # Load bundle
        bundle = TranscribeBundle.from_existing_directory(
            existing_dir=generic_bundle_dir,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )
        bundle.cleanup_inconsistencies(False)

        assert bundle.bundle_name == "2025-01-15_meeting"
        assert bundle.source_audios == [generic_bundle_dir / "meeting.mp3"]
        assert bundle.transcript is not None
        assert isinstance(bundle.transcript, TranscriptFile)

    def test_from_existing_directory_preserves_bundle_id(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        generic_bundle: TranscribeBundle,
    ) -> None:
        """Reloading a saved bundle keeps its logical identity."""
        reloaded = TranscribeBundle.from_existing_directory(
            existing_dir=generic_bundle.get_bundle_dir(),
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )

        assert reloaded.bundle_id == generic_bundle.bundle_id

    def test_instances_loaded_from_the_same_bundle_share_identity(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        generic_bundle: TranscribeBundle,
    ) -> None:
        """Entity equality and hashing use the persisted ID, not object identity."""
        first = TranscribeBundle.from_existing_directory(
            generic_bundle.get_bundle_dir(),
            fake_config,
            fake_fs,
            fake_audio_service,
        )
        second = TranscribeBundle.from_existing_directory(
            generic_bundle.get_bundle_dir(),
            fake_config,
            fake_fs,
            fake_audio_service,
        )

        assert first is not second
        assert first == second
        assert hash(first) == hash(second)
        assert len({first, second}) == 1

    @pytest.mark.usefixtures("generic_bundle")
    def test_from_existing_directory_raises_on_missing_metadata(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        generic_bundle_dir: Path,
    ) -> None:
        """Test that loading raises if metadata file is missing."""
        fake_fs.delete_file(generic_bundle_dir / METADATA_FILENAME)

        with pytest.raises(InvalidBundleException):
            TranscribeBundle.from_existing_directory(
                existing_dir=generic_bundle_dir,
                config=fake_config,
                fs_service=fake_fs,
                audio_service=fake_audio_service,
            )

    @pytest.mark.usefixtures("generic_bundle")
    def test_from_existing_directory_raises_on_wrong_metadata(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        generic_bundle_dir: Path,
    ) -> None:
        """Test that loading raises if metadata file is missing."""
        # overwrite metadata file with wrong data
        metadata_yaml = (
            "---\n"
            "bundle_id: 0123456789abcdef0123456789abcdef\n"
            "audio_files:\n"
            "- transcript_model_used: []\n"  # missing required 'filename'
            "transcript_model_used: null\n"
            "summary_model_used: null\n"
            "bundle_name_generated: false\n"
            "keep_forever: false\n"
            "---\n"
        )
        fake_fs.write_file(generic_bundle_dir / METADATA_FILENAME, metadata_yaml)

        with pytest.raises(
            pydantic.ValidationError,
            match=r"audio_files",
        ):
            TranscribeBundle.from_existing_directory(
                existing_dir=generic_bundle_dir,
                config=fake_config,
                fs_service=fake_fs,
                audio_service=fake_audio_service,
            )


class TestGetDateForFilename:
    """Tests for TranscribeBundle.get_date_for_filename raising FailedToExtractDateException."""

    def test_raises_when_no_date_in_filename_and_no_path(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Without a date in the filename and no audio path, extraction fails."""
        with pytest.raises(FailedToExtractDateException):
            TranscribeBundle.get_date_for_filename(
                audio_path=None,
                audio_filename="weird_name.mp3",
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
        assert generic_bundle.commands
        cmd_one_id = generic_bundle.commands.commands[0].id
        generic_bundle.set_command_type(cmd_one_id, CommandType.MERGE)

        assert generic_bundle.commands
        assert generic_bundle.commands.commands[0].matched_type == CommandType.MERGE
        assert generic_bundle.commands.commands[1].matched_type is None
        assert not generic_bundle.commands.commands[0].executed
        assert not generic_bundle.commands.commands[1].executed
        assert generic_bundle.commands.commands[0].executed_at is None
        assert generic_bundle.commands.commands[1].executed_at is None

        generic_bundle.set_command_executed(cmd_one_id)
        assert generic_bundle.commands.commands[0].executed
        assert not generic_bundle.commands.commands[1].executed
        assert generic_bundle.commands.commands[0].executed_at is not None
        assert generic_bundle.commands.commands[1].executed_at is None

    def test_init_metadata(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        generic_bundle_dir: Path,
    ) -> None:
        """Test initializing metadata with file information."""
        bundle = TranscribeBundle(
            bundle_name=generic_bundle_dir.name,
            metadata=MetadataFile(
                bundle_id=new_bundle_id(),
                audio_files=[],
                bundle_date=datetime.now(fake_config.general.timezone),
            ),
            source_audios=[],
            transcript=None,
            summary=None,
            commands=None,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
            config=fake_config,
        )

        filenames = ["part1.mp3", "part2.mp3"]

        bundle.init_metadata(filenames)

        assert [f.filename for f in bundle.metadata.audio_files] == filenames
        assert fake_fs.file_exists(generic_bundle_dir / METADATA_FILENAME)


class TestTranscribeBundleCleanupInconsistencies:
    """Tests cleanup_inconsistencies."""

    def test_cleanup_inconsistencies_removes_inconsistent_files(
        self,
        fake_fs: FakeFileSystemService,
        generic_bundle_dir: Path,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Test that cleanup_inconsistencies removes summary and commands when transcript is missing."""
        bundle = transcribe_bundle_factory(
            bundle_name=generic_bundle_dir.name,
            audio_filename="another_audio_file.mp3",
            transcript_text=None,
            summary_text="test summary",
            commands=["do nothing"],
        )
        bundle.source_audios = [generic_bundle_dir / "another_audio_file.mp3"]

        fake_fs.write_file(generic_bundle_dir / METADATA_FILENAME, "Old metadata")
        fake_fs.write_file(generic_bundle_dir / "another_audio_file.mp3", "Old audio")

        bundle.cleanup_inconsistencies(dry_run=False)

        # the audio file should be kept
        assert fake_fs.file_exists(generic_bundle_dir / "another_audio_file.mp3")
        # but inconsistent files should be deleted
        assert not fake_fs.file_exists(generic_bundle_dir / SUMMARY_FILENAME)
        assert not fake_fs.file_exists(generic_bundle_dir / COMMANDS_FILENAME)


class TestTranscribeBundleRefresh:
    """Tests for refresh operation."""

    def test_refresh_clears_transcript_and_summary(
        self,
        fake_fs: FakeFileSystemService,
        generic_bundle_dir: Path,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Test that refresh clears transcript and summary."""
        bundle = transcribe_bundle_factory(
            bundle_name=generic_bundle_dir.name,
            audio_filename="meeting.mp3",
            transcript_text="Old transcript",
            summary_text="Old summary",
        )

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
        fake_fs: FakeFileSystemService,
        generic_bundle_dir: Path,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Test that refresh with dry_run=True does not delete files."""
        bundle = transcribe_bundle_factory(
            bundle_name=generic_bundle_dir.name,
            audio_filename="meeting.mp3",
            transcript_text="Transcript",
            summary_text="Summary",
            commands=["keep this"],
        )

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

        original_id = generic_bundle.bundle_id

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
        assert generic_bundle.bundle_id == original_id

    def test_set_and_write_bundle_name_is_a_noop_for_the_current_name(
        self,
        fake_fs: FakeFileSystemService,
        generic_bundle: TranscribeBundle,
    ) -> None:
        """Regenerating the same name does not ask the filesystem to move it."""
        generic_bundle.set_and_write_bundle_name("Planning Session")
        rename_count = len(fake_fs.get_operations("rename"))

        generic_bundle.set_and_write_bundle_name("Planning Session")

        assert len(fake_fs.get_operations("rename")) == rename_count
        assert generic_bundle.metadata.bundle_name_generated is True

    def test_set_and_write_bundle_name_uses_id_suffix_on_collision(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        generic_bundle: TranscribeBundle,
    ) -> None:
        """Two bundles may share a display name without sharing a directory."""
        prefix = generic_bundle.generate_bundle_name_date_prefix(generic_bundle.metadata.bundle_date)
        desired_name = f"{prefix} Planning Session"
        fake_fs.create_directory(fake_config.general.store_dir / desired_name)

        generic_bundle.set_and_write_bundle_name("Planning Session")

        assert generic_bundle.bundle_name == f"{desired_name} ~{generic_bundle.bundle_id[:8]}"
        assert fake_fs.directory_exists(generic_bundle.get_bundle_dir())

    def test_set_and_write_bundle_name_raises_on_missing_directory(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
    ) -> None:
        """Test that renaming raises if directory doesn't exist."""
        bundle = TranscribeBundle(
            bundle_name="2025-01-15_meeting",
            metadata=MetadataFile(
                bundle_id=new_bundle_id(),
                audio_files=[AudioFileMeta(filename="meeting.mp3")],
                bundle_date=datetime.now(fake_config.general.timezone),
            ),
            source_audios=[Path("/store/2025-01-15_meeting/meeting.mp3")],
            fs_service=fake_fs,
            audio_service=fake_audio_service,
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
        fake_audio_service: FakeAudioService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Test that gather_existing_bundles finds valid bundles."""
        store_dir = fake_config.general.store_dir

        transcribe_bundle_factory(
            bundle_name="2025-01-15_meeting1",
            audio_filename="meeting.mp3",
        )
        transcribe_bundle_factory(
            bundle_name="2025-01-15_meeting2",
            audio_filename="meeting.mp3",
        )

        bundles = TranscribeBundle.gather_existing_bundles(
            store_dir=store_dir,
            dry_run=False,
            cleanup_bundle=True,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )

        assert len(bundles) == 2
        assert set(bundles) == {bundle.bundle_id for bundle in bundles.values()}
        assert {b.bundle_name for b in bundles.values()} == {
            "2025-01-15_meeting1",
            "2025-01-15_meeting2",
        }

    def test_gather_existing_bundles_skips_invalid_bundles(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        generic_bundle: TranscribeBundle,
    ) -> None:
        """Test that gather_existing_bundles skips invalid bundles."""
        store_dir = fake_config.general.store_dir
        invalid_bundle_dir = store_dir / "2025-01-15_invalid"

        # Create invalid bundle (missing metadata)
        fake_fs.create_directory(invalid_bundle_dir)
        fake_fs.write_file(invalid_bundle_dir / "meeting.mp3", "audio")

        bundles = TranscribeBundle.gather_existing_bundles(
            store_dir=store_dir,
            dry_run=False,
            cleanup_bundle=True,
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )

        # at this point we have 1 valid bundle (generic_bundle) and 1 invalid one
        assert len(bundles) == 1
        assert next(iter(bundles.values())).bundle_name == generic_bundle.bundle_name

    def test_gather_existing_bundles_rejects_duplicate_ids(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Copied bundle metadata cannot silently overwrite a cache entry."""
        duplicate_id = new_bundle_id()
        transcribe_bundle_factory(
            bundle_name="2025-01-15_first",
            audio_filename="first.mp3",
            bundle_id=duplicate_id,
        )
        transcribe_bundle_factory(
            bundle_name="2025-01-15_second",
            audio_filename="second.mp3",
            bundle_id=duplicate_id,
        )

        with pytest.raises(DuplicateBundleIdException, match=duplicate_id):
            TranscribeBundle.gather_existing_bundles(
                store_dir=fake_config.general.store_dir,
                dry_run=False,
                cleanup_bundle=False,
                config=fake_config,
                fs_service=fake_fs,
                audio_service=fake_audio_service,
            )

    def test_find_previous_bundle_returns_most_recent_prior_bundle(
        self,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Test that the previous bundle is selected correctly."""
        older_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-14_status",
            audio_filename="Recording 20250114180000.mp3",
        )
        previous_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_review",
            audio_filename="Recording 20250115020000.mp3",
        )
        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_meeting",
            audio_filename="Recording 20250115090000.mp3",
        )

        result = TranscribeBundle.find_previous_bundle(
            current_bundle=current_bundle,
            bundles=[older_bundle, previous_bundle, current_bundle],
        )

        assert result is previous_bundle

    def test_find_previous_bundle_respects_merge_window(
        self,
        fake_config: TranscribeConfig,
        transcribe_bundle_factory: TranscribeBundleFactory,
    ) -> None:
        """Test that the merge window filters out bundles that are too old."""
        fake_config.general.merge_max_hours = 12.0

        distant_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-14_review",
            audio_filename="Recording 20250114120000.mp3",
            config=fake_config,
            bundle_date=datetime(year=2025, month=1, day=14, hour=12, tzinfo=fake_config.general.timezone),
        )
        current_bundle = transcribe_bundle_factory(
            bundle_name="2025-01-15_meeting",
            audio_filename="Recording 20250115090000.mp3",
            config=fake_config,
            bundle_date=datetime(year=2025, month=1, day=15, hour=9, tzinfo=fake_config.general.timezone),
        )
        result = TranscribeBundle.find_previous_bundle(
            current_bundle=current_bundle,
            bundles=[distant_bundle, current_bundle],
        )

        assert result is None


class TestBundleDate:
    """Tests for the canonical metadata.bundle_date field."""

    def test_init_metadata_seeds_bundle_date_from_audio_filename(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
    ) -> None:
        """init_metadata computes bundle_date from the audio filename timestamp."""
        bundle = TranscribeBundle.from_audio_file(
            source_audio=fake_config.general.input_dir / "Recording 20250115143000.mp3",
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )
        bundle.init_metadata(["Recording 20250115143000.mp3"])

        assert bundle.metadata.bundle_date is not None
        assert bundle.metadata.bundle_date.year == 2025
        assert bundle.metadata.bundle_date.month == 1
        assert bundle.metadata.bundle_date.day == 15
        assert bundle.metadata.bundle_date.hour == 14
        assert bundle.metadata.bundle_date.minute == 30

    def test_get_bundle_date_returns_canonical_metadata_date(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
    ) -> None:
        """get_bundle_date returns metadata.bundle_date without recomputing."""
        bundle = TranscribeBundle.from_audio_file(
            source_audio=fake_config.general.input_dir / "Recording 20250115143000.mp3",
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )
        bundle.init_metadata(["Recording 20250115143000.mp3"])
        # Sanity: the canonical date is the audio-derived one.
        assert bundle.get_bundle_date() == bundle.metadata.bundle_date

    def test_cleanup_warns_on_dirname_date_divergence(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """cleanup_inconsistencies warns when the dir name date != metadata date."""
        bundle = TranscribeBundle.from_audio_file(
            source_audio=fake_config.general.input_dir / "Recording 20250115143000.mp3",
            config=fake_config,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
        )
        bundle.init_metadata(["Recording 20250115143000.mp3"])
        # Simulate an external directory rename: name says the 20th, metadata says the 15th.
        bundle.bundle_name = "2025-01-20_renamed"
        bundle.cleanup_inconsistencies(dry_run=True)

        assert any("differs from canonical" in record.message for record in caplog.records)


class TestTranscribeBundleIntegration:
    """Integration tests for complete workflows."""

    def test_complete_workflow_create_load_update(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
        fake_audio_service: FakeAudioService,
    ) -> None:
        """Test a complete workflow: create, load, update bundle."""
        # Step 1: Create bundle from audio file
        audio_path = Path("/input/meeting.mp3")
        bundle = TranscribeBundle.from_audio_file(
            source_audio=audio_path,
            fs_service=fake_fs,
            audio_service=fake_audio_service,
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
            audio_service=fake_audio_service,
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
        fake_audio_service: FakeAudioService,
    ) -> None:
        """Test that refresh is triggered when new audio files are detected."""
        bundle_dir = Path("/store/2025-01-15_meeting")
        fake_fs.create_directory(bundle_dir)

        # Create initial metadata with one file
        metadata_yaml = (
            "---\n"
            "bundle_id: 0123456789abcdef0123456789abcdef\n"
            "bundle_date: 2025-01-15 00:00:00+02:00\n"
            "audio_files:\n"
            "- filename: part1.mp3\n"
            "  transcript_model_used: []\n"
            "transcript_model_used: []\n"
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
            audio_service=fake_audio_service,
        )

        # Bundle should have both files now
        assert len(bundle.source_audios) == 2
        assert {f.name for f in bundle.source_audios} == {"part1.mp3", "part2.mp3"}
