"""Tests for MetadataFile raising InvalidMetadataFileException."""

from pathlib import Path

import pytest

from tests.fake_file_system import FakeFileSystemService
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
