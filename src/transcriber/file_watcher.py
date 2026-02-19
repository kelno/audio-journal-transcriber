from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Optional

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from .globals import is_handled_audio_file
from .logger import logger


Callback = Callable[[Path], None]


class FileWatcher(FileSystemEventHandler):
    """
    Debounced filesystem watcher.

    We intentionally avoid per-file stability tracking (size checks, timestamps,
    multiple timers). Instead, we treat filesystem activity as a noisy signal and
    wait for a quiet period before processing files.

    If stricter guarantees are required (per-file atomicity, large directories,
    high-frequency ingestion), this design may need to be replaced with a more
    granular state machine.

    delay: Fire events after no file changes in the whole input_dir for given time
    """

    def __init__(self, input_dir: Path, callback: Callback, stable_delay: float = 5.0) -> None:
        self.input_dir = input_dir
        self.callback = callback
        self.stable_delay = stable_delay

        self._observer = Observer()
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self._observer.schedule(self, self.input_dir.as_posix(), recursive=True)
        self._observer.start()
        logger.info(f"Started watching directory: {self.input_dir}")

    def stop(self) -> None:
        logger.info("Stopping FileWatcher...")

        self._observer.stop()
        self._observer.join()

        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None

        logger.info("FileWatcher stopped")

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return

        logger.debug(f"Filesystem event detected: {event.src_path}")

        with self._lock:
            if self._timer:
                self._timer.cancel()
                logger.debug("Reset debounce timer")

            self._timer = threading.Timer(self.stable_delay, self._process_files)
            self._timer.start()

    def _process_files(self) -> None:
        logger.debug(f"Quiet period reached, scanning directory: {self.input_dir}")

        for file_path in self.input_dir.rglob("*"):
            if not file_path.is_file():
                continue

            if not is_handled_audio_file(file_path.suffix):
                continue

            logger.debug(f"Processing file: {file_path}")
            self.callback(file_path)
