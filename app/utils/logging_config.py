import logging
import os

LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

def get_logger(name: str) -> logging.Logger:
    """Return a logger for a specific router."""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    log_file = os.path.join(LOG_DIR, f"{name}.log")
    
    # Avoid adding multiple handlers if logger already exists
    if not logger.handlers:
        file_handler = logging.FileHandler(log_file)
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger
