"""Canonical runtime plumbing for logging and run control."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any, Callable, Dict, Optional


TECHNICAL_LOGGER_NAME = "item_journal.technical"


def get_technical_logger() -> logging.Logger:
    return logging.getLogger(TECHNICAL_LOGGER_NAME)


def configure_logging(
    verbose: bool = False,
    log_file: str | None = None,
) -> tuple[logging.Logger, Path | None]:
    path = Path(log_file).expanduser() if log_file and str(log_file).strip() else None
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("item_journal")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if path is not None:
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    technical_logger = get_technical_logger()
    technical_logger.setLevel(logging.DEBUG)
    technical_logger.handlers.clear()
    technical_logger.propagate = False
    if path is not None:
        technical_path = path.with_name(f"{path.stem}.technical{path.suffix or '.log'}")
        technical_handler = logging.FileHandler(technical_path, encoding="utf-8")
        technical_handler.setLevel(logging.DEBUG)
        technical_handler.setFormatter(formatter)
        technical_logger.addHandler(technical_handler)
    else:
        technical_logger.addHandler(logging.NullHandler())

    return logger, path


@dataclass
class RunControl:
    stop_mode: str = "run-to-batch-stop"
    stop_event: Event = field(default_factory=Event)
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None

    def request_stop(self) -> None:
        self.stop_event.set()

    def is_stop_requested(self) -> bool:
        return self.stop_event.is_set()

    def publish_progress(self, payload: Dict[str, Any]) -> None:
        if self.progress_callback is None:
            return
        try:
            self.progress_callback(payload)
        except Exception:
            return

