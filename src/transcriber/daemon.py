import threading
import time
from pathlib import Path

from transcriber.transcribe_bundle_job import BundleJobs

from .logger import logger

from .file_watcher import FileWatcher
from .audio_transcriber import AudioTranscriber
from .retry_manager import RetryManager


def run_daemon_mode(transcriber: AudioTranscriber, unprocessed_bundles: list[BundleJobs]):
    """Start file watch and process new files after filesystem quiet period."""

    logger.info("Starting daemon mode")

    lock: threading.Lock = threading.Lock()
    unprocessed = unprocessed_bundles
    retry_manager = RetryManager(initial_delay=1.0, max_delay=3600.0)

    def process_watched_file(_file_path: Path):
        nonlocal unprocessed
        with lock:
            # Reset retry delay on file changes
            retry_manager.reset_delay()
            unprocessed = transcriber.run()

    # trigger process_watched_file on any file changes, after state is stable for 5s
    input_dir: Path = transcriber.config.general.input_dir
    watcher = FileWatcher(input_dir, process_watched_file, stable_delay=5.0)
    watcher.start()

    try:
        while True:
            delay = retry_manager.get_current_delay()
            time.sleep(delay)
            with lock:
                if len(unprocessed) > 0:
                    logger.info(f"Retrying {len(unprocessed)} failed bundles...")
                    unprocessed = transcriber.run()
                    if len(unprocessed) == 0:
                        retry_manager.reset_delay()
                    else:
                        retry_manager.increase_delay()
    except KeyboardInterrupt:
        logger.info("Stopping daemon mode...")
        watcher.stop()
