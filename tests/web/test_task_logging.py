import logging
from concurrent.futures import ThreadPoolExecutor

from pdf_trans.web.task_logging import TaskLogHandler


def test_handler_captures_child_thread_logs_for_active_task(
    repository,
    tmp_path,
) -> None:
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    handler = TaskLogHandler(repository, tmp_path)
    package_logger = logging.getLogger("pdf_trans")
    logger = logging.getLogger("pdf_trans.translation")
    package_logger.addHandler(handler)
    package_logger.setLevel(logging.INFO)
    try:
        handler.activate("a")
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(logger.info, "子线程完成").result()
        logging.getLogger("pdf_trans.web.routes").info("请求日志")
        handler.deactivate()
        logger.info("任务外日志")
    finally:
        package_logger.removeHandler(handler)

    assert [log.message for log in repository.list_logs("a")] == [
        "子线程完成"
    ]
    log_text = (tmp_path / "tasks/a/task.log").read_text(encoding="utf-8")
    assert "[INFO] 子线程完成" in log_text
    assert "\033[" not in log_text
    assert "请求日志" not in log_text
    assert "任务外日志" not in log_text


def test_handler_still_writes_file_when_database_logging_fails(
    tmp_path,
    capsys,
) -> None:
    class BrokenRepository:
        def append_log(self, task_id, level, message):
            raise RuntimeError("database unavailable")

    handler = TaskLogHandler(BrokenRepository(), tmp_path)
    record = logging.LogRecord(
        "pdf_trans.workflow",
        logging.WARNING,
        __file__,
        1,
        "需要排查",
        (),
        None,
    )

    handler.activate("task-id")
    handler.emit(record)
    handler.deactivate()

    assert "[WARNING] 需要排查" in (
        tmp_path / "tasks/task-id/task.log"
    ).read_text(encoding="utf-8")
    assert "无法持久化任务数据库日志" in capsys.readouterr().err
