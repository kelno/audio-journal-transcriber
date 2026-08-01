"""Tests for the explicit bundle title-state migration."""

from pathlib import Path

import pytest

from scripts.migrate_bundle_title_state import migrate_store
from tests.fake_file_system import FakeFileSystemService
from transcriber.bundle_title import BundleTitleState
from transcriber.constants import METADATA_FILENAME
from transcriber.files.metadata import MetadataFile


def _write_metadata(
    fake_fs: FakeFileSystemService,
    bundle_dir: Path,
    title_fields: str,
    *,
    body: str = "\n",
) -> Path:
    """Write otherwise-valid metadata with caller-controlled title fields."""
    fake_fs.create_directory(bundle_dir)
    metadata_path = bundle_dir / METADATA_FILENAME
    fake_fs.write_file(
        metadata_path,
        "---\n"
        "bundle_id: 0123456789abcdef0123456789abcdef\n"
        "audio_files: []\n"
        "bundle_date: 2025-01-15T12:00:00+00:00\n"
        f"{title_fields}"
        f"---{body}",
    )
    return metadata_path


class TestMigrateBundleTitleState:
    """Verify dry-run, apply, and all-or-nothing migration behavior."""

    def test_dry_run_reports_without_writing(
        self,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Dry-run validates and counts legacy metadata without changing it."""
        store_dir = fake_fs.config.general.store_dir
        metadata_path = _write_metadata(
            fake_fs,
            store_dir / "legacy",
            "bundle_name_generated: false\n",
        )
        original_content = fake_fs.read_file(metadata_path)

        result = migrate_store(store_dir, apply=False, fs_service=fake_fs)

        assert result.scanned == 1
        assert result.migrated == 1
        assert result.already_current == 0
        assert fake_fs.read_file(metadata_path) == original_content

    @pytest.mark.parametrize(
        ("legacy_generated", "expected_state"),
        [
            (False, BundleTitleState.PENDING),
            (True, BundleTitleState.GENERATED),
        ],
    )
    def test_apply_replaces_legacy_boolean_and_preserves_body(
        self,
        legacy_generated: bool,
        expected_state: BundleTitleState,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """Apply writes the equivalent title state and retains body content."""
        store_dir = fake_fs.config.general.store_dir
        metadata_path = _write_metadata(
            fake_fs,
            store_dir / f"legacy-{legacy_generated}",
            f"bundle_name_generated: {str(legacy_generated).lower()}\n",
            body="\nPreserved body\n",
        )

        result = migrate_store(store_dir, apply=True, fs_service=fake_fs)

        migrated_content = fake_fs.read_file(metadata_path)
        metadata = MetadataFile.from_file(metadata_path, fake_fs)
        assert result.migrated == 1
        assert metadata.bundle_title_state is expected_state
        assert "bundle_name_generated" not in migrated_content
        assert "Preserved body" in migrated_content

    def test_current_metadata_is_not_rewritten(
        self,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """A valid state field is counted as current and left byte-for-byte intact."""
        store_dir = fake_fs.config.general.store_dir
        metadata_path = _write_metadata(
            fake_fs,
            store_dir / "current",
            "bundle_title_state: manual\n",
        )
        original_content = fake_fs.read_file(metadata_path)

        result = migrate_store(store_dir, apply=True, fs_service=fake_fs)

        assert result.already_current == 1
        assert result.migrated == 0
        assert fake_fs.read_file(metadata_path) == original_content

    def test_ambiguous_metadata_prevents_all_writes(
        self,
        fake_fs: FakeFileSystemService,
    ) -> None:
        """The full store is validated before any prepared migration is written."""
        store_dir = fake_fs.config.general.store_dir
        valid_path = _write_metadata(
            fake_fs,
            store_dir / "a-valid",
            "bundle_name_generated: false\n",
        )
        original_valid_content = fake_fs.read_file(valid_path)
        _write_metadata(
            fake_fs,
            store_dir / "b-ambiguous",
            "bundle_name_generated: true\nbundle_title_state: generated\n",
        )

        with pytest.raises(ValueError, match="contains both"):
            migrate_store(store_dir, apply=True, fs_service=fake_fs)

        assert fake_fs.read_file(valid_path) == original_valid_content
