import argparse
from pathlib import Path
import os

from transcriber.audio_transcriber import AudioTranscriber
from transcriber.config import TranscribeConfig
from transcriber.logger import configure_logger

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Transcribe audio files in Obsidian Vault.")
    parser.add_argument("obsidian_root", type=str, help="The Obsidian root directory path")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate transcription without actually processing audio files. Will still create directories.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging output.",
    )

    args = parser.parse_args()
    configure_logger(args.debug)

    obsidian_root = Path(args.obsidian_root)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config = TranscribeConfig.from_config_dir(Path(script_dir))

    transcriber = AudioTranscriber(config=config, obsidian_root=obsidian_root, dry_run=args.dry_run)
    transcriber.run()
