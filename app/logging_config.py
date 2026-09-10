import logging


def setup_logging(log_level: str) -> None:
    """Configure basic structured logging for the application."""
    # Prevent duplicate handlers if create_app is called multiple times
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # Use a structured-like format (key=value)
    formatter = logging.Formatter('level=%(levelname)s logger=%(name)s message="%(message)s"')

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger.setLevel(numeric_level)
    root_logger.addHandler(handler)
