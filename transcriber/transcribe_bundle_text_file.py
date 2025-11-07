from dataclasses import dataclass, asdict
from pathlib import Path

import yaml

@dataclass
class TranscribeTextFileProps:
    """
    Class representing possible frontmatter values in the text file.
    Probably will need splitting if summary & transcript file props start getting more specific.
    """
    model: str

@dataclass
class TranscribeTextFile:
    props: TranscribeTextFileProps
    content: str

    @classmethod
    def from_file(cls, path: Path) -> "TranscribeTextFile":
        """
        Parse a markdown file into frontmatter + content.
        Returns (frontmatter_obj, markdown_content)
        """
        text = path.read_text(encoding="utf-8")
        front, content = cls._split_frontmatter(text)
        data = yaml.safe_load(front) if front else {}
        return cls(TranscribeTextFileProps(**data), content.strip()) # This will explode if unknown props are set?

    @classmethod
    def _split_frontmatter(cls, text: str) -> tuple[str | None, str]:
        """
        Returns (frontmatter_yaml, body_text)
        """
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                _, front, body = parts
                return front.strip(), body
        return None, text

    def to_text(self) -> str:
        """
        Combine this frontmatter and body content into full markdown text.
        """
        yaml_text = yaml.safe_dump(asdict(self.props), sort_keys=False).strip()
        return f"---\n{yaml_text}\n---\n\n{self.content.strip()}\n"

    def write(self, path: Path) -> None:
        """Write full markdown file with this frontmatter."""
        path.write_text(self.to_text(), encoding="utf-8")
