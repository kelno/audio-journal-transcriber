import argparse
from pathlib import Path
import sys

from transcriber.audio_transcriber import AudioTranscriber
from transcriber.config import TranscribeConfig
from transcriber.logger import configure_logger, get_logger

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Move, transcribe and summarize audio files into processed bundles.")
    parser.add_argument("input_dir", type=str, help="The directory to take audio files from")
    parser.add_argument(
        "--store",
        type=str,
        help="The managed transcription directory where files will be moved and processed into bundles (created if not existing)",
    )
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
    configure_logger(args.debug)
    logger = get_logger()

    if not AudioTranscriber.validate_environment():
        logger.error("Exiting after missing requirements")
        sys.exit(1)

    input_dir = Path(args.input_dir)
    store_dir = Path(args.store)
    script_dir = Path(__file__).parent
    config = TranscribeConfig.from_config_dir(script_dir)

    transcriber = AudioTranscriber(config=config, store_dir=store_dir, dry_run=args.dry_run)
    transcriber.run(input_dir)
