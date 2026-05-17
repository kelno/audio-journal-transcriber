from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from transcriber.constants import METADATA_FILENAME


@dataclass
class Metadata:
    """A bundle metadata is kept in this single database file as yaml data"""

    original_audio_filenames: list[str]
    audio_length: float | None = None
    transcript_model_used: str | None = None
    summary_model_used: str | None = None
    bundle_name_generated: bool = False
    keep_forever: bool = False


@dataclass
class MetadataFile(Metadata):
    @staticmethod
    def _split_frontmatter(text: str) -> str | None:
        """
        Returns (frontmatter_yaml, body_text)
        """
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 2:
                _, front, _body = parts
                return front.strip()
        return None

    @classmethod
    def from_file(cls, meta_file: Path) -> "MetadataFile":
        text = meta_file.read_text(encoding="utf-8")
        if front := cls._split_frontmatter(text):
            data = yaml.safe_load(front)
        else:
            raise ValueError(
                f"Invalid metadata file {meta_file}, failed to find frontmatter"
            )
        # if data has original_audio_filename" key, that's the old format, so we need to convert it to the new format
        if "original_audio_filename" in data:
            data["original_audio_filenames"] = [data["original_audio_filename"]]
            del data["original_audio_filename"]

        return MetadataFile(**data)

    def write(self, bundle_dir: Path):
        yaml_text = (
            f"---\n{yaml.safe_dump(asdict(self), sort_keys=False).strip()}\n---\n"
        )
        output_file = bundle_dir / METADATA_FILENAME
        output_file.write_text(yaml_text, encoding="utf-8")
