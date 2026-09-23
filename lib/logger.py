"""Colored logging utilities."""

import logging

class ColoredFormatter(logging.Formatter):
    """A logging formatter that adds ANSI color codes to log levels."""

    # ANSI color codes matching pytest's colors
    COLORS = {
        logging.CRITICAL: "\033[91m",  # Red
        logging.ERROR: "\033[91m",     # Red
        logging.WARNING: "\033[93m",   # Yellow
        logging.INFO: "\033[92m",      # Green
        logging.DEBUG: "\033[95m",     # Magenta/Purple
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelno, "")
        original_levelname = record.levelname
        if color:
            record.levelname = f'{color}{record.levelname}{self.RESET}'
        result = super().format(record)
        record.levelname = original_levelname
        return result


def setup_colored_logging(level: int = logging.INFO) -> None:
    """Set up logging with colored formatter."""
    logger = logging.getLogger()
    logger.setLevel(level)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    handler = logging.StreamHandler()
    formatter = ColoredFormatter(
        fmt="%(asctime)s.%(msecs)03d %(levelname)s %(message)s", datefmt="%b %d %H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
