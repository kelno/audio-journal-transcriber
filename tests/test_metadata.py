"""Tests for bundle metadata validation and persistence."""

from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.fake_file_system import FakeFileSystemService
from transcriber.bundle_title import BundleTitleState
from transcriber.config import TranscribeConfig
from transcriber.exception import InvalidMetadataFileException
from transcriber.files.metadata import MetadataFile


class TestMetadataFileFromInvalidFile:
    """Tests for MetadataFile.from_file raising InvalidMetadataFileException."""

    def test_raises_when_no_frontmatter(
        self,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """A metadata file without YAML frontmatter is rejected."""
        meta_file = Path("/fake/store/2025-01-15_meeting") / "metadata.yaml"
        # Plain text, no leading "---" frontmatter block.
        fake_fs.write_file(meta_file, "This is not a valid metadata file\n")

        with pytest.raises(InvalidMetadataFileException):
            MetadataFile.from_file(meta_file, fake_fs)

    def test_raises_when_bundle_id_is_missing(
        self,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Pydantic rejects legacy metadata until the store is migrated."""
        meta_file = Path("/fake/store/2025-01-15_meeting/_metadata.md")
        fake_fs.write_file(
            meta_file,
            "---\naudio_files: []\nbundle_date: 2025-01-15T12:00:00+00:00\nbundle_title_state: pending\n---\n",
        )

        with pytest.raises(ValidationError, match="bundle_id"):
            MetadataFile.from_file(meta_file, fake_fs)


class TestMetadataFileBundleId:
    """Tests for the persistent bundle identifier."""

    def test_bundle_id_round_trips_through_metadata_file(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Writing and loading metadata preserves the exact bundle ID."""
        bundle_dir = fake_config.general.store_dir / "2025-01-15_meeting"
        bundle_id = "0123456789abcdef0123456789abcdef"
        metadata = MetadataFile(
            bundle_id=bundle_id,
            bundle_date=datetime.now(fake_config.general.timezone),
            bundle_title_state=BundleTitleState.PENDING,
        )

        metadata.write(bundle_dir, fake_fs)
        loaded = MetadataFile.from_file(bundle_dir / "_metadata.md", fake_fs)

        assert loaded.bundle_id == bundle_id

    def test_bundle_id_must_be_a_lowercase_uuid_hex(self, fake_config: TranscribeConfig) -> None:
        """Malformed IDs are rejected instead of entering cache keys."""
        with pytest.raises(ValidationError, match="bundle_id"):
            MetadataFile(
                bundle_id="not-a-valid-bundle-id",
                bundle_date=datetime.now(fake_config.general.timezone),
                bundle_title_state=BundleTitleState.PENDING,
            )


class TestMetadataFileBundleTitleState:
    """Tests for strict title-state persistence."""

    def test_rejects_legacy_bundle_name_generated_boolean(
        self,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Legacy metadata must be upgraded with the explicit migration script."""
        meta_file = Path("/fake/store/legacy") / "_metadata.md"
        fake_fs.write_file(
            meta_file,
            "---\n"
            "bundle_id: 0123456789abcdef0123456789abcdef\n"
            "audio_files: []\n"
            "bundle_date: 2025-01-15T12:00:00+00:00\n"
            "bundle_name_generated: true\n"
            "---\n",
        )

        with pytest.raises(ValidationError, match="bundle_title_state"):
            MetadataFile.from_file(meta_file, fake_fs)

    def test_writes_only_bundle_title_state(
        self,
        fake_config: TranscribeConfig,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """New metadata writes the enum state without retaining the legacy boolean."""
        bundle_dir = fake_config.general.store_dir / "manual"
        metadata = MetadataFile(
            bundle_id="0123456789abcdef0123456789abcdef",
            bundle_date=datetime.now(fake_config.general.timezone),
            bundle_title_state=BundleTitleState.MANUAL,
        )

        metadata.write(bundle_dir, fake_fs)
        serialized = fake_fs.read_file(bundle_dir / "_metadata.md")

        assert "bundle_title_state: manual" in serialized
        assert "bundle_name_generated" not in serialized
