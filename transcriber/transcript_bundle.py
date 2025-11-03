from dataclasses import dataclass
from pathlib import Path
import shutil

from transcriber.utils import ensure_directory_exists
from transcriber.logger import get_logger

logger = get_logger()

@dataclass
class TranscriptBundle:
    bundle_name: str # has to be a valid filename
    source_audio: Path
    transcript: str
    ai_summary: str

    # @classmethod
    # def from_existing_directory(cls, _existing_dir: Path):
    #     """Create a ResultFile instance from an existing result file."""

    #     transcript = "dummy transcript"  # Replace with actual reading logic
    #     ai_summary = "dummy AI summary"  # Replace with actual reading logic
    #     return cls("dummy", transcript, ai_summary)

    def get_text_file_path(self, complete_subdir: Path) -> Path:
        """Get the full path of the result file."""
        # For now we just return the initial filepath, but we want to use an AI generated one later
        return complete_subdir / self.bundle_name / ".md"

    def get_obsidian_properties(self) -> str:
        """Generate Obsidian frontmatter properties for the transcript bundle."""

        return "---\nstatus: test\n---\n"

    def write(self, complete_subdir: Path):
        """Write content to the result file."""

        ensure_directory_exists(complete_subdir)

        final_audio_path = complete_subdir / self.bundle_name / self.source_audio.suffix
        logger.debug(f"Moving audio file from [{self.source_audio}] to [{final_audio_path}]")
        shutil.move(self.source_audio, final_audio_path)

        text_filepath = self.get_text_file_path(complete_subdir)
        with open(text_filepath, 'w', encoding='utf-8') as f:
            f.write(self.get_obsidian_properties())
            f.write("\n")
            f.write("Transcript:\n")
            f.write(self.transcript)
            f.write("\n\n---\n\n")
            f.write("AI Summary:\n")
            f.write(self.ai_summary)

        logger.debug(f"Wrote content to {text_filepath}")
