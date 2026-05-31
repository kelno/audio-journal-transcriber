"""Tests for file system backup deletion functionality."""

import tempfile
from pathlib import Path

import pytest

from transcriber.config import TranscribeConfig
from transcriber.constants import DELETED_DIR_NAME
from transcriber.files.file_system import RealFileSystemService


class TestFileSystemBackupDeletion:
    """Test backup deletion functionality in RealFileSystemService."""

    def _create_config_with_backup_enabled(self, fake_config: TranscribeConfig, temp_path: Path, store_dir: Path) -> None:
        """Helper to modify fake_config to enable backup deletion and set paths."""
        fake_config.general.input_dir = temp_path
        fake_config.general.store_dir = store_dir
        fake_config.general.backup_deletion = True

    def _create_config_with_backup_disabled(self, fake_config: TranscribeConfig, temp_path: Path, store_dir: Path) -> None:
        """Helper to modify fake_config to disable backup deletion and set paths."""
        fake_config.general.input_dir = temp_path
        fake_config.general.store_dir = store_dir
        fake_config.general.backup_deletion = False

    def test_delete_file_with_backup_enabled_moves_to_deleted_dir(self, fake_config: TranscribeConfig) -> None:
        """Test that deleting a file with backup enabled moves it to _deleted directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store_dir = temp_path / "store"
            store_dir.mkdir()

            # Create test file
            test_file = temp_path / "test.txt"
            test_file.write_text("test content")

            # Configure fake_config for this test
            self._create_config_with_backup_enabled(fake_config, temp_path, store_dir)

            fs = RealFileSystemService(fake_config)

            # Delete the file
            fs.delete_file(test_file)

            # Verify original file is gone
            assert not test_file.exists()

            # Verify backup exists in store_dir/_deleted
            backup_file = store_dir / DELETED_DIR_NAME / "test.txt"
            assert backup_file.exists()
            assert backup_file.read_text() == "test content"

    def test_delete_file_with_backup_disabled_deletes_permanently(self, fake_config: TranscribeConfig) -> None:
        """Test that deleting a file with backup disabled removes it permanently."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create test file
            test_file = temp_path / "test.txt"
            test_file.write_text("test content")

            # Configure fake_config for this test
            self._create_config_with_backup_disabled(fake_config, temp_path, temp_path / "store")

            fs = RealFileSystemService(fake_config)

            # Delete the file
            fs.delete_file(test_file)

            # Verify file is permanently deleted
            assert not test_file.exists()

    def test_delete_file_preserves_relative_structure(self, fake_config: TranscribeConfig) -> None:
        """Test that relative directory structure is preserved in backup."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store_dir = temp_path / "store"
            store_dir.mkdir()

            # Create nested structure within store_dir
            test_file = store_dir / "subdir" / "nested" / "test.txt"
            test_file.parent.mkdir(parents=True, exist_ok=True)
            test_file.write_text("nested content")

            # Configure fake_config for this test
            self._create_config_with_backup_enabled(fake_config, temp_path, store_dir)

            fs = RealFileSystemService(fake_config)

            # Delete the file
            fs.delete_file(test_file)

            # Verify backup preserves structure
            backup_file = store_dir / DELETED_DIR_NAME / "subdir" / "nested" / "test.txt"
            assert backup_file.exists()
            assert backup_file.read_text() == "nested content"

    def test_delete_file_handles_name_collisions(self, fake_config: TranscribeConfig) -> None:
        """Test that name collisions are handled by appending counters."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store_dir = temp_path / "store"
            store_dir.mkdir()

            # Create two files with same name in different locations
            test_file1 = temp_path / "test.txt"
            test_file1.write_text("content 1")

            test_file2 = temp_path / "other" / "test.txt"
            test_file2.parent.mkdir(parents=True, exist_ok=True)
            test_file2.write_text("content 2")

            # Configure fake_config for this test
            self._create_config_with_backup_enabled(fake_config, temp_path, store_dir)

            fs = RealFileSystemService(fake_config)

            # Delete both files
            fs.delete_file(test_file1)
            fs.delete_file(test_file2)

            # Verify both files are backed up with unique names
            backup_dir = store_dir / DELETED_DIR_NAME
            backup_files = list(backup_dir.glob("*test*"))

            assert len(backup_files) == 2
            contents = [f.read_text() for f in backup_files]
            assert "content 1" in contents
            assert "content 2" in contents

    def test_delete_directory_with_backup_enabled_moves_to_deleted_dir(self, fake_config: TranscribeConfig) -> None:
        """Test that deleting a directory with backup enabled moves it to _deleted directory."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            store_dir = temp_path / "store"
            store_dir.mkdir()

            # Create test directory with contents
            test_dir = store_dir / "test_dir"
            test_dir.mkdir()
            (test_dir / "subfile.txt").write_text("subfile content")
            subdir = test_dir / "subdir"
            subdir.mkdir()
            (subdir / "nested.txt").write_text("nested content")

            # Configure fake_config for this test
            self._create_config_with_backup_enabled(fake_config, temp_path, store_dir)

            fs = RealFileSystemService(fake_config)

            # Delete the directory
            fs.delete_directory(test_dir)

            # Verify original directory is gone
            assert not test_dir.exists()

            # Verify backup exists with all contents
            backup_dir = store_dir / DELETED_DIR_NAME / "test_dir"
            assert backup_dir.exists()
            assert (backup_dir / "subfile.txt").exists()
            assert (backup_dir / "subfile.txt").read_text() == "subfile content"
            assert (backup_dir / "subdir" / "nested.txt").exists()
            assert (backup_dir / "subdir" / "nested.txt").read_text() == "nested content"

    def test_delete_directory_with_backup_disabled_deletes_permanently(self, fake_config: TranscribeConfig) -> None:
        """Test that deleting a directory with backup disabled removes it permanently."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            # Create test directory
            test_dir = temp_path / "test_dir"
            test_dir.mkdir()
            (test_dir / "subfile.txt").write_text("content")

            # Configure fake_config for this test
            self._create_config_with_backup_disabled(fake_config, temp_path, temp_path / "store")

            fs = RealFileSystemService(fake_config)

            # Delete the directory
            fs.delete_directory(test_dir)

            # Verify directory is permanently deleted
            assert not test_dir.exists()

    def test_delete_nonexistent_file_raises_error(self, fake_config: TranscribeConfig) -> None:
        """Test that deleting a nonexistent file raises FileNotFoundError."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)

            nonexistent_file = temp_path / "nonexistent.txt"

            self._create_config_with_backup_disabled(fake_config, temp_path, temp_path / "store")

            fs = RealFileSystemService(fake_config)

            # Verify FileNotFoundError is raised
            with pytest.raises(FileNotFoundError, match="File not found"):
                fs.delete_file(nonexistent_file)
