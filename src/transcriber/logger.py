import logging
import coloredlogs


def get_logger():
    return logging.getLogger("transcriber")


def configure_logger(debug: bool = False, log_file: str | None = None):
    """Initialize the logger with console and file handlers."""
    new_logger = get_logger()
    coloredlogs.install(
        level="DEBUG" if debug else "INFO",
        logger=new_logger,
        fmt="%(asctime)s,%(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if log_file is not None:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        new_logger.addHandler(file_handler)


logger = logging.getLogger("transcriber")
