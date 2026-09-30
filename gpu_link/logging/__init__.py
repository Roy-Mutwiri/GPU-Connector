"""Rotating local logs. Callers never include connection secrets."""

import logging
from logging.handlers import RotatingFileHandler

from gpu_link.security import data_dir


def configure():
    logger = logging.getLogger("gpu_link")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(data_dir() / "gpu-link.log", maxBytes=2_000_000,
                                      backupCount=4, encoding="utf-8")
        handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%H:%M:%S"))
        logger.addHandler(handler)
    return logger
