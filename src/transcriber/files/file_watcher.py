from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import final, override

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from transcriber.logger import logger

Callback = Callable[[Path], None]
"""
Callback type alias for file processing functions.

Args:
    file_path: Path to the file that should be processed
"""


@final
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
        """
        Initialize the FileWatcher.

        Args:
            input_dir: Directory path to watch for file changes
            callback: Function to call when files are ready for processing
            stable_delay: Time in seconds to wait for filesystem quiet period
                         before processing files (default: 5.0 seconds)

        """
        self.input_dir = input_dir
        self.callback = callback
        self.stable_delay = stable_delay

        self._observer = Observer()
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start watching the input directory for filesystem events."""
        self._observer.schedule(self, self.input_dir.as_posix(), recursive=True)
        self._observer.start()
        logger.info(f"Started watching directory: {self.input_dir}")

    def stop(self) -> None:
        """Stop watching the directory and clean up resources."""
        logger.info("Stopping FileWatcher...")

        self._observer.stop()
        self._observer.join()

        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None

        logger.info("FileWatcher stopped")

    @override
    def on_any_event(self, event: FileSystemEvent) -> None:
        """Handle filesystem events by resetting the debounce timer.

        Args:
            event: Filesystem event that triggered this callback

        """
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
        """
        Process all files in the watched directory after the quiet period.

        This method is called by the debounce timer when no filesystem events
        have occurred for the stable_delay period. It scans the entire directory
        recursively and calls the callback function for each file found.
        """
        logger.debug(f"Quiet period reached, scanning directory: {self.input_dir}")

        for file_path in self.input_dir.rglob("*"):
            if not file_path.is_file():
                continue

            logger.debug(f"Processing file: {file_path}")
            self.callback(file_path)
