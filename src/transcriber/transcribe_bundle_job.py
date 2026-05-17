import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from transcriber.audio_manipulation import AudioManipulation

from .ai_manager import AIManager
from .config import get_config
from .exception import EmptyTranscriptException, TooShortException
from .logger import logger
from .transcribe_bundle import TranscribeBundle
from .utils import ensure_directory_exists, file_is_in_directory_tree


@dataclass
class TranscribeBundleJob(ABC):
    """Abstract base class for all job types."""

    bundle: TranscribeBundle
    dry_run: bool

    @abstractmethod
    def run(self, ai_manager: AIManager):
        """Perform the job's main work."""

    def __str__(self):
        return f"{self.__class__.__name__}({self.bundle.get_bundle_name()})"


# Job list for a single bundle
type BundleJobs = list[TranscribeBundleJob]


@dataclass
class CreateBundleJob(TranscribeBundleJob):
    """Move all audio files into the bundle directory."""

    def run(self, ai_manager: AIManager):
        if not self.bundle.source_audios:
            raise FileNotFoundError("Bundle has no audio files set")

        final_audio_paths = self.bundle.get_bundle_audio_paths()
        audio_lengths = []
        files_to_move = []

        # Check all files and validate
        for source_path, final_path in zip(
            self.bundle.source_audios, final_audio_paths
        ):
            if source_path != final_path:
                files_to_move.append((source_path, final_path))

        if files_to_move:
            logger.info(f"Moving {len(files_to_move)} audio file(s) into bundle")

            if not self.dry_run:
                min_length = get_config().general.min_length_seconds

                # Validate all files
                for source_path, _ in files_to_move:
                    audio_length = AudioManipulation.get_audio_duration(source_path)
                    if min_length and audio_length < min_length:
                        raise TooShortException(
                            source_audio=source_path,
                            audio_length=audio_length,
                            min_length=min_length,
                        )
                    audio_lengths.append(audio_length)

                # Move all files
                ensure_directory_exists(final_audio_paths[0].parent)
                for source_path, final_path in files_to_move:
                    shutil.move(source_path, final_path)

                self.bundle.update_audio_paths([Path(p) for _, p in files_to_move])
                self.bundle.init_metadata(
                    filenames=[p.name for _, p in files_to_move],
                    audio_lengths=audio_lengths,
                )


@dataclass
class TranscriptionJob(TranscribeBundleJob):
    def run(self, ai_manager: AIManager):
        if not self.bundle.source_audios:
            raise FileNotFoundError(f"{self}: Bundle has no audio files set")

        logger.info(f"Transcribing {len(self.bundle.source_audios)} audio file(s)")

        if not self.dry_run:
            transcripts = []
            for audio_path in self.bundle.source_audios:
                logger.debug(f"Transcribing {audio_path}")
                transcript_content = ai_manager.transcribe_audio(audio_path)
                if transcript_content.strip() == "":
                    raise EmptyTranscriptException(
                        f"{self}: Transcription of {audio_path} resulted in empty transcript"
                    )
                transcripts.append(transcript_content)

            # Concatenate all transcripts
            concatenated = "\n\n".join(transcripts)

            # Update original_audio_filenames to reflect current state
            # This ensures that if new files were added, metadata is synced
            self.bundle.metadata.original_audio_filenames = [
                audio.name for audio in self.bundle.source_audios
            ]
            self.bundle.metadata.write(self.bundle.get_bundle_dir())

            # Write transcript after metadata is updated
            self.bundle.set_and_write_transcript(concatenated, get_config().audio.model)


@dataclass
class SummaryJob(TranscribeBundleJob):
    """Generate AI summary for the bundle based on transcript."""

    def run(self, ai_manager: AIManager):
        logger.info(f"Summarizing {self.bundle.get_bundle_name()}")

        if self.dry_run:
            return

        if not self.bundle.transcript:
            raise ValueError(f"{self}: Cannot generate ai summary without transcript")

        summary_content = ai_manager.get_ai_summary(self.bundle.transcript.text)
        logger.info(f"Summary complete: {summary_content[:40]}")
        self.bundle.set_and_write_summary(summary_content, get_config().text.model)


@dataclass
class BundleNameJob(TranscribeBundleJob):
    """Generate AI-based bundle name based on summary."""

    def run(self, ai_manager: AIManager):
        logger.info(f"Generating buddle name for {self.bundle.get_bundle_name()}")
        if self.dry_run:
            return

        if not self.bundle.summary:
            raise ValueError("Cannot generate bundle name without AI summary.")

        try:
            bundle_name = ai_manager.get_bundle_name_summary(self.bundle.summary.text)
        except Exception:
            logger.error(f"{self}: Failed to generate bundle name")
            raise

        logger.info(f"Generated bundle name: {bundle_name}")
        self.bundle.set_and_write_bundle_name(bundle_name)


@dataclass
class DeleteAudioFileJob(TranscribeBundleJob):
    """Remove all audio files"""

    def run(self, ai_manager: AIManager):
        if not self.bundle.source_audios:
            raise FileNotFoundError("Bundle has no audio files set")

        logger.info(f"Deleting {len(self.bundle.source_audios)} audio file(s)")

        if not self.dry_run:
            for audio_path in self.bundle.source_audios:
                logger.debug(f"Deleting {audio_path}")
                audio_path.unlink()

            self.bundle.update_audio_paths(None)


# Moved here to avoid circular imports
def gather_bundle_jobs(
    bundle: TranscribeBundle, store_dir: Path, dry_run: bool
) -> BundleJobs:
    """Gather transcription jobs from this bundle. Jobs needs to be run in order."""
    jobs = []

    bundle_name = bundle.get_bundle_name()
    logger.debug(f"Gathering jobs for bundle: [{bundle_name}]")

    if bundle.source_audios:
        is_new_audio = not file_is_in_directory_tree(bundle.source_audios[0], store_dir)
        if is_new_audio:
            job = CreateBundleJob(bundle, dry_run)
            jobs.append(job)

        if not is_new_audio and bundle.audio_source_needs_removal(
            get_config().general.delete_source_audio_after_days
        ):
            job = DeleteAudioFileJob(bundle, dry_run)
            jobs.append(job)
        elif not bundle.transcript:
            job = TranscriptionJob(bundle, dry_run)
            jobs.append(job)

    if get_config().text.summary_enabled:
        if not bundle.summary:
            # First check if transcript exists or TranscriptionJob is scheduled
            transcript_exists_or_scheduled = bundle.transcript is not None or any(
                isinstance(j, TranscriptionJob) for j in jobs
            )
            if transcript_exists_or_scheduled:
                job = SummaryJob(bundle, dry_run)
                jobs.append(job)

        # always needs to be done after summary as this relies on summary content
        if bundle.needs_naming():
            job = BundleNameJob(bundle, dry_run)
            jobs.append(job)

    return jobs
