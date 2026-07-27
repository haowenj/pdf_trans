import io
import logging

import pytest

from pdf_trans.logging_utils import configure_logging, logged_stage


@pytest.fixture(autouse=True)
def reset_package_logger():
    package_logger = logging.getLogger("pdf_trans")
    yield
    package_logger.handlers.clear()
    package_logger.setLevel(logging.NOTSET)
    package_logger.propagate = True


@pytest.mark.parametrize(
    ("level", "prefix"),
    [
        (logging.INFO, "\033[32m[INFO]\033[0m"),
        (logging.WARNING, "\033[33m[WARN]\033[0m"),
        (logging.ERROR, "\033[31m[ERROR]\033[0m"),
    ],
)
def test_configured_logger_uses_colored_level_prefix(level, prefix):
    stream = io.StringIO()
    configure_logging(stream)
    logger = logging.getLogger("pdf_trans.test")

    logger.log(level, "消息")

    assert stream.getvalue() == f"{prefix} 消息\n"


def test_configure_logging_replaces_handlers_instead_of_duplicating():
    first = io.StringIO()
    second = io.StringIO()
    configure_logging(first)
    configure_logging(second)

    logging.getLogger("pdf_trans.test").info("一次")

    assert first.getvalue() == ""
    assert second.getvalue().count("一次") == 1


def test_logged_stage_reports_action_result_and_elapsed_time(
    monkeypatch,
    caplog,
):
    times = iter([10.0, 12.5])
    monkeypatch.setattr(
        "pdf_trans.logging_utils.time.perf_counter",
        lambda: next(times),
    )
    logger = logging.getLogger("pdf_trans.stage-test")
    caplog.set_level(logging.INFO, logger=logger.name)

    with logged_stage(logger, "清洗数据", "移除页眉和空 text") as stage:
        stage.set_result("输入 10 项，过滤 2 项，保留 8 项")

    messages = [record.getMessage() for record in caplog.records]
    assert "开始清洗数据：移除页眉和空 text" in messages
    assert (
        "清洗数据完成：输入 10 项，过滤 2 项，保留 8 项，耗时 2.50 秒"
        in messages
    )


def test_logged_stage_reports_error_and_elapsed_time(monkeypatch, caplog):
    times = iter([3.0, 4.25])
    monkeypatch.setattr(
        "pdf_trans.logging_utils.time.perf_counter",
        lambda: next(times),
    )
    logger = logging.getLogger("pdf_trans.stage-error-test")
    caplog.set_level(logging.ERROR, logger=logger.name)

    with pytest.raises(RuntimeError, match="boom"):
        with logged_stage(logger, "翻译", "并发调用模型"):
            raise RuntimeError("boom")

    assert any(
        record.levelno == logging.ERROR
        and record.getMessage() == "翻译失败：boom，耗时 1.25 秒"
        for record in caplog.records
    )
