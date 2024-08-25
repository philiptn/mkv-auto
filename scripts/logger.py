import logging
import os
import time

_global_logger = None  # Different name to avoid shadowing


def setup_logger(log_file):
    global _global_logger  # Use the global variable

    if _global_logger is not None:
        return _global_logger  # Return the existing logger if already set up

    # Set up the logger (as before)
    logger = logging.getLogger("logger")
    logger.setLevel(logging.DEBUG)

    logging.addLevelName(25, "COLOR")
    logging.Formatter.converter = time.gmtime

    # Add the COLOR method to the logger
    def color(self, message, *args, **kwargs):
        if self.isEnabledFor(25):
            self._log(25, message, args, **kwargs)
    logging.Logger.color = color

    # Create a custom filter that only allows messages of a specific level
    class SpecificLevelFilter(logging.Filter):
        def __init__(self, level):
            self.level = level

        def filter(self, record):
            return record.levelno == self.level

    log_dir, log_filename = os.path.split(log_file)
    log_basename, log_extension = os.path.splitext(log_filename)

    plain_file_handler = logging.FileHandler(os.path.join(log_dir, f"{log_basename}{log_extension}"), mode='a')
    colored_file_handler = logging.FileHandler(os.path.join(log_dir, f"{log_basename}-color{log_extension}"), mode='a')
    debug_file_handler = logging.FileHandler(os.path.join(log_dir, f"{log_basename}-debug{log_extension}"), mode='a')

    # Handler for plain text logging (INFO level)
    plain_file_handler.setLevel(logging.INFO)
    plain_formatter = logging.Formatter('%(message)s')
    plain_file_handler.setFormatter(plain_formatter)
    plain_file_handler.addFilter(SpecificLevelFilter(logging.INFO))

    # Handler for colored logging (COLOR level)
    colored_file_handler.setLevel(25)
    colored_formatter = logging.Formatter(f'%(message)s')
    colored_file_handler.setFormatter(colored_formatter)
    colored_file_handler.addFilter(SpecificLevelFilter(25))

    # Handler for debug logging (DEBUG level)
    debug_file_handler.setLevel(logging.DEBUG)
    debug_formatter = logging.Formatter('[%(levelname)s] %(message)s')
    debug_file_handler.setFormatter(debug_formatter)
    debug_file_handler.addFilter(SpecificLevelFilter(logging.DEBUG))

    logger.addHandler(plain_file_handler)
    logger.addHandler(colored_file_handler)
    logger.addHandler(debug_file_handler)

    _global_logger = logger  # Assign to the global variable

    return _global_logger


def get_custom_logger():
    if _global_logger is None:
        raise ValueError("Logger is not set up. Please call setup_logger(log_file) first.")
    return _global_logger
