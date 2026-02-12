import time
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from .logger import logger
from .globals import is_handled_audio_file


class FileWatcher:
    def __init__(self, input_dir: Path, callback, stability_delay: int = 5):
        self.input_dir = input_dir
        self.callback = callback
        self.stability_delay = stability_delay
        self.observer = Observer()
        self.file_states = {}

    def start(self):
        event_handler = FileSystemEventHandler()
        event_handler.on_created = self.on_file_created
        event_handler.on_modified = self.on_file_modified
        self.observer.schedule(event_handler, self.input_dir.as_posix(), recursive=True)
        self.observer.start()
        logger.info(f"Started watching directory: {self.input_dir}")

    def stop(self):
        self.observer.stop()
        self.observer.join()
        logger.info("Stopped watching directory")

    def on_file_created(self, event):
        if not event.is_directory:
            file_path = Path(event.src_path)
            if is_handled_audio_file(file_path.suffix):
                logger.info(f"Detected new file: {file_path}")
                self.file_states[file_path] = {"last_modified": time.time(), "last_size": file_path.stat().st_size}

    def on_file_modified(self, event):
        if not event.is_directory:
            file_path = Path(event.src_path)
            if file_path in self.file_states:
                current_size = file_path.stat().st_size
                if current_size != self.file_states[file_path]["last_size"]:
                    self.file_states[file_path]["last_modified"] = time.time()
                    self.file_states[file_path]["last_size"] = current_size
                else:
                    if time.time() - self.file_states[file_path]["last_modified"] > self.stability_delay:
                        logger.debug(f"File stable, processing: {file_path}")
                        self.callback(file_path)
                        del self.file_states[file_path]
