"""Service file logging shared by the Python USAGI processes."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable

LEVELS = (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL)


def default_log_root() -> Path:
    configured = os.environ.get("USAGI_LOG_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    for candidate in (Path.cwd(), *Path(__file__).resolve().parents):
        if (candidate / "apps").is_dir() and (candidate / "usagi-agent").is_dir():
            return candidate / "log"
    return Path.cwd() / "log"


class DailyLevelFileHandler(logging.Handler):
    """Append one exact log level to a UTF-8 file, rolling at midnight."""

    def __init__(self, directory: Path, level: int) -> None:
        super().__init__(level)
        self.directory = directory
        self.exact_level = level

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno != self.exact_level:
            return
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            day = datetime.fromtimestamp(record.created).astimezone().date().isoformat()
            level = logging.getLevelName(self.exact_level).lower()
            with (self.directory / f"{day}.{level}.log").open("a", encoding="utf-8") as output:
                output.write(self.format(record) + "\n")
        except Exception:
            self.handleError(record)


def _handlers(service_name: str, root: Path) -> list[logging.Handler]:
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(filename)s:%(lineno)d %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    handlers: list[logging.Handler] = []
    for level in LEVELS:
        handler = DailyLevelFileHandler(root / service_name, level)
        handler.setFormatter(formatter)
        handlers.append(handler)
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    handlers.append(console)
    return handlers


def configure_service_logging(
    service_name: str,
    logger_names: Iterable[str],
    *,
    log_root: str | Path | None = None,
    level: int = logging.DEBUG,
) -> Path:
    if log_root:
        configured = Path(log_root).expanduser()
        root = configured.resolve() if configured.is_absolute() else (default_log_root().parent / configured).resolve()
    else:
        root = default_log_root()
    for name in logger_names:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        for handler in _handlers(service_name, root):
            logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = False
    return root / service_name
