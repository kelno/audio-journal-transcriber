from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from transcriber.constants import METADATA_FILENAME
from transcriber.files.file_system import FileSystemService


@dataclass
class Metadata:
    """A bundle metadata is kept in this single database file as YAML data."""

    original_audio_filenames: list[str]
    audio_length: float | None = None
    transcript_model_used: str | None = None
    summary_model_used: str | None = None
    bundle_name_generated: bool = False
    keep_forever: bool = False


@dataclass
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
            ValueError: If the metadata file has invalid format.

        """
        text = fs_service.read_file(meta_file)
        if frontmatter := cls._extract_frontmatter(text):
            data = yaml.safe_load(frontmatter)
        else:
            error_msg = f"Invalid metadata file {meta_file}, failed to find frontmatter"
            raise ValueError(error_msg)

        # If data has "original_audio_filename" key, that's the old format,
        # so we need to convert it to the new format
        if "original_audio_filename" in data:
            data["original_audio_filenames"] = [data["original_audio_filename"]]
            del data["original_audio_filename"]

        return MetadataFile(**data)

    def write(self, bundle_dir: Path, fs_service: FileSystemService) -> None:
        """Write the metadata to a file in the bundle directory.

        Args:
            bundle_dir: Path to the bundle directory where the metadata should be written.
            fs_service: FileSystemService instance for writing files.

        """
        yaml_text = (
            f"---\n{yaml.safe_dump(asdict(self), sort_keys=False).strip()}\n---\n"
        )
        output_file = bundle_dir / METADATA_FILENAME
        fs_service.write_file(output_file, yaml_text)
