import argparse
import sys

from transcriber.audio_transcriber import AudioTranscriber
from transcriber.config import TranscribeConfig
from transcriber.logger import configure_logger, get_logger


def main():
    parser = argparse.ArgumentParser(description="Move, transcribe and summarize audio files into processed bundles.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate transcription and other jobs without actually writing changes. Will still create directories",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging output.",
    )

    args = parser.parse_args()

    dry_run = args.dry_run
    debug = args.debug

    configure_logger(debug)
    logger = get_logger()

    if not AudioTranscriber.validate_environment():
        logger.error("Exiting after missing requirements")
        sys.exit(1)

    config = TranscribeConfig()  # type: ignore

    transcriber = AudioTranscriber(config=config, dry_run=dry_run)
    transcriber.run()
