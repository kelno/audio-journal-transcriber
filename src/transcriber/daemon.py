import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from transcriber.audio_transcriber import AudioTranscriber
from transcriber.config import TranscribeConfig
from transcriber.files.file_watcher import FileWatcher
from transcriber.logger import logger
from transcriber.retry_manager import RetryManager
from transcriber.transcribe_bundle_job import BundleJobs

# Maximum time allowed between processing attempts.
#
# This guarantees periodic processing even when no filesystem activity is
# detected.
MAX_PROCESSING_INTERVAL = 3600.0

# Maximum delay between retries of failed processing attempts.
#
# Retries use increasing delays up to this limit to avoid continuously
# retrying bundles that cannot currently succeed.
MAX_RETRY_DELAY = 3600.0


@dataclass
class DaemonState:
    """
    Runtime state maintained by the daemon loop.

    State changes are performed by a single thread, so synchronization is not
    required for these fields.
    """

    unprocessed: list[BundleJobs]
    retry_manager: RetryManager
    next_hourly_run: float


class TranscriptionDaemon:
    """Coordinates filesystem watching and transcription scheduling.

    Processing can be triggered by:
    - Filesystem activity
    - Retry deadlines after failed processing
    - Hourly fallback deadlines

    File watching and processing are separated:
    - The watcher only reports activity
    - The daemon loop owns processing and state updates.
    """

    def __init__(
        self,
        transcriber: AudioTranscriber,
        unprocessed_bundles: list[BundleJobs],
        config: TranscribeConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize the transcription daemon.

        Args:
            transcriber: Transcriber instance used to process pending bundles.
            unprocessed_bundles: Bundles that should be retried when the daemon starts.
            config: Daemon configuration, including the input directory to watch.
            clock:
                Function returning the current monotonic time in seconds.
                Injected mainly to make scheduling behavior deterministic in
                tests. Defaults to time.monotonic.
                Using monotonic time makes this independent from wall-clock
                changes such as NTP corrections or manual clock adjustments.

        """
        self.transcriber: AudioTranscriber = transcriber
        self.clock: Callable[[], float] = clock

        self.state: DaemonState = DaemonState(
            unprocessed=unprocessed_bundles,
            retry_manager=RetryManager(
                initial_delay=1.0,
                max_delay=MAX_RETRY_DELAY,
            ),
            next_hourly_run=clock() + MAX_PROCESSING_INTERVAL,
        )

        # Signals that filesystem activity has occurred.
        #
        # The event is only used for communication between threads. Processing
        # itself remains in the daemon loop so that state updates happen in a
        # single execution context.
        self.work_event: threading.Event = threading.Event()

        # Used to interrupt the waiting loop during shutdown.
        self.stop_event: threading.Event = threading.Event()

        self.watcher: FileWatcher = FileWatcher(
            config.general.input_dir,
            self._on_files_changed,
            stable_delay=5.0,
        )

    def run(self) -> None:
        """Start the daemon lifecycle.

        The method blocks until stop() is called or the process is interrupted.
        """
        logger.info("Starting daemon mode")

        self.watcher.start()

        try:
            while not self.stop_event.is_set():
                work_triggered: bool = self._wait_for_work()

                if self.stop_event.is_set():
                    break

                self._process_if_needed(work_triggered)

        finally:
            logger.info("Stopping daemon mode...")
            self.watcher.stop()

    def stop(self) -> None:
        """Request daemon shutdown."""
        self.stop_event.set()

        # Wake the wait loop immediately if it is currently blocked.
        self.work_event.set()

    def _on_files_changed(self, _file_path: Path) -> None:
        """
        Receive filesystem notifications from FileWatcher.

        The callback only signals that work may be available. Processing is
        performed by the daemon loop.
        """
        self.work_event.set()

    def _wait_for_work(self) -> bool:
        """Wait until filesystem activity occurs or a scheduled deadline expires.

        Returns:
            Whether filesystem activity triggered the wake-up.

        """
        triggered: bool = self.work_event.wait(
            timeout=self._seconds_until_next_run(),
        )
        self.work_event.clear()  # Reset the notification after consuming it

        return triggered

    def _process_if_needed(self, work_triggered: bool) -> None:
        """Decide whether processing should happen.

        Filesystem activity takes priority because it represents new input.
        Retry and hourly processing happen when their respective deadlines are
        reached.
        """
        now: float = self.clock()

        if work_triggered:
            self._process("Filesystem changes detected")

        elif self.state.unprocessed:
            self._process(f"Retrying {len(self.state.unprocessed)} failed bundles...")

        elif now >= self.state.next_hourly_run:
            self._process("Running hourly fallback processing...")

    def _process(self, reason: str) -> None:
        """Execute transcription and update scheduling state.

        This is the only location where transcription is executed.
        """
        logger.info(reason)

        self.state.unprocessed = self.transcriber.run()

        # The fallback interval is measured from the end of processing.
        self.state.next_hourly_run = self.clock() + MAX_PROCESSING_INTERVAL

        if self.state.unprocessed:
            self.state.retry_manager.increase_delay()

            logger.info(
                "Retrying in %.1fs",
                self.state.retry_manager.get_current_delay(),
            )

        else:
            self.state.retry_manager.reset_delay()

    def _seconds_until_next_run(self) -> float:
        """Calculate how long the daemon can wait before the next scheduled run.

        The daemon wakes at whichever deadline comes first:
        - retry delay
        - hourly fallback deadline

        Filesystem activity can interrupt this wait immediately.
        """
        now: float = self.clock()

        retry_delay: float = self.state.retry_manager.get_current_delay()
        hourly_delay: float = max(
            0.0,
            self.state.next_hourly_run - now,
        )

        return min(
            retry_delay,
            hourly_delay,
        )
