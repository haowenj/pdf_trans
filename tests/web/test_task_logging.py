import logging
from concurrent.futures import ThreadPoolExecutor

from pdf_trans.web.task_logging import TaskLogHandler


def test_handler_captures_child_thread_logs_for_active_task(
    repository,
) -> None:
    repository.create_task("a", "a.pdf", "tasks/a/upload/source.pdf")
    handler = TaskLogHandler(repository)
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
