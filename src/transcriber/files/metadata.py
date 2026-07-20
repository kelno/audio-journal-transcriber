from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from transcriber.constants import METADATA_FILENAME
from transcriber.exception import InvalidMetadataFileException
from transcriber.files.file_system import FileSystemService


class AudioFileMeta(BaseModel):
    """Metadata for a single source audio file within a bundle.

    Stores the filename and the transcript model(s) used for that specific file.
    Keeping this per-file (rather than a single bundle-level value) is more correct:
    different files in a merged bundle may have been transcribed by different models.
    """

    filename: str
    transcript_model_used: list[str] = Field(default_factory=list)


class Metadata(BaseModel):
    """A bundle metadata is kept in this single database file as YAML data.

    We use pydantic BaseModel to ensure validity as data loaded from the file could be bad.
    """

    audio_files: list[AudioFileMeta] = Field(default_factory=list)
    # Canonical date of the bundle (precise datetime up to seconds).
    # Computed once at creation from the first audio file's timestamp.
    bundle_date: datetime
    summary_model_used: str | None = None
    bundle_name_generated: bool = False
    keep_forever: bool = False
    # sha256[:16] of the effective custom_context.md content. None means the hash
    # has not been computed yet (e.g. metadata written before this field existed).
    summary_context_hash: str | None = None


class MetadataFile(Metadata):
    @staticmethod
    def _extract_frontmatter(text: str) -> str | None:
        """Split text into frontmatter YAML and body text.

        Args:
            text: The full text to extract frontmatter from.

        Returns:
            str | None: The frontmatter YAML if found, None otherwise.

        """
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 2:
                _, front, _body = parts
                return front.strip()
        return None

    @classmethod
    def from_file(
        cls,
        meta_file: Path,
        fs_service: FileSystemService,
    ) -> "MetadataFile":
        """Create a MetadataFile instance from a metadata file.

        Args:
            meta_file: Path to the metadata file.
            fs_service: FileSystemService instance for reading files.

        Returns:
            MetadataFile: The loaded metadata.

        Raises:
            InvalidMetadataFileException: If the metadata file has invalid format.

        """
        text = fs_service.read_file(meta_file)
        if frontmatter := cls._extract_frontmatter(text):
            data = yaml.safe_load(frontmatter)
        else:
            error_msg = f"Invalid metadata file {meta_file}, failed to find frontmatter"
            raise InvalidMetadataFileException(error_msg)

        return MetadataFile.model_validate(data)

    def write(self, bundle_dir: Path, fs_service: FileSystemService) -> None:
        """Write the metadata to a file in the bundle directory.

        Args:
            bundle_dir: Path to the bundle directory where the metadata should be written.
            fs_service: FileSystemService instance for writing files.

        """
        yaml_text = f"---\n{yaml.safe_dump(self.model_dump(), sort_keys=False).strip()}\n---\n"
        output_file = bundle_dir / METADATA_FILENAME
        fs_service.write_file(output_file, yaml_text)
