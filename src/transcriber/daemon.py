import time
from pathlib import Path

from .logger import logger

from .file_watcher import FileWatcher
from .audio_transcriber import AudioTranscriber


def run_daemon_mode(transcriber: AudioTranscriber):
    """Start file watch and process new files"""

    logger.info("Starting daemon mode")
    input_dir = transcriber.config.general.input_dir
    store_dir = transcriber.config.general.store_dir

    def process_file(file_path: Path):
        transcriber.process_single_file(file_path, store_dir)

    watcher = FileWatcher(input_dir, process_file)
    watcher.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        watcher.stop()
        logger.info("Daemon mode stopped by user")
