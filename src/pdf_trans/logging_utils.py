from __future__ import annotations

import logging
import sys
import time
from types import TracebackType
from typing import TextIO

PACKAGE_LOGGER_NAME = "pdf_trans"

_PREFIXES = {
    logging.INFO: "\033[32m[INFO]\033[0m",
    logging.WARNING: "\033[33m[WARN]\033[0m",
    logging.ERROR: "\033[31m[ERROR]\033[0m",
    logging.CRITICAL: "\033[31m[ERROR]\033[0m",
}


class ColorLevelFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        prefix = _PREFIXES.get(record.levelno, f"[{record.levelname}]")
        return f"{prefix} {record.getMessage()}"


def configure_logging(stream: TextIO | None = None) -> None:
    package_logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(ColorLevelFormatter())
    package_logger.handlers.clear()
    package_logger.addHandler(handler)
    package_logger.setLevel(logging.INFO)
    package_logger.propagate = False


class StageLog:
    def __init__(
        self,
        logger: logging.Logger,
        name: str,
        action: str,
    ) -> None:
        self.logger = logger
        self.name = name
        self.action = action
        self._started = 0.0
        self._result = ""

    def set_result(self, result: str) -> None:
        self._result = result

    def __enter__(self) -> "StageLog":
        self._started = time.perf_counter()
        self.logger.info("开始%s：%s", self.name, self.action)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        elapsed = time.perf_counter() - self._started
        if exc is not None:
            self.logger.error(
                "%s失败：%s，耗时 %.2f 秒",
                self.name,
                exc,
                elapsed,
            )
            return False
        detail = f"：{self._result}" if self._result else ""
        self.logger.info(
            "%s完成%s，耗时 %.2f 秒",
            self.name,
            detail,
            elapsed,
        )
        return False


def logged_stage(
    logger: logging.Logger,
    name: str,
    action: str,
) -> StageLog:
    return StageLog(logger, name, action)
