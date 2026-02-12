import argparse
import sys

from transcriber.ai_manager import AIManager
from transcriber.daemon import run_daemon_mode

from .audio_transcriber import AudioTranscriber
from .config import TranscribeConfig
from .logger import configure_logger, logger


def main():
    parser = argparse.ArgumentParser(description="Move, transcribe and summarize audio files into processed bundles.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate transcription and other jobs without actually writing changes. Will still create directories.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging output.",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run in daemon mode, watching file changes and processing continuously.",
    )
    args = parser.parse_args()

    dry_run = args.dry_run
    debug = args.debug
    daemon = args.daemon

    configure_logger(debug)

    if not AudioTranscriber.validate_environment():
        logger.error("Exiting after missing requirements")
        sys.exit(1)

    config = TranscribeConfig()  # type: ignore

    ai_manager = AIManager(config)
    transcriber = AudioTranscriber(config=config, dry_run=dry_run, ai_manager=ai_manager)
    transcriber.run()

    if daemon:
        run_daemon_mode(transcriber)
