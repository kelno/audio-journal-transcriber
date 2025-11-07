import logging
import coloredlogs

LOG_FILE = "transcribe.log"


def get_logger():
    return logging.getLogger("transcriber")


def configure_logger(debug: bool = False):
    """Initialize the logger with console and file handlers."""
    new_logger = get_logger()
    coloredlogs.install(
        level="DEBUG" if debug else "INFO",
        logger=new_logger,
        fmt="%(asctime)s,%(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s - %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
    )
    new_logger.addHandler(file_handler)
