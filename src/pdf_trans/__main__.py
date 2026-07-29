from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Sequence

from pdf_trans.client import (
    DEFAULT_MINERU_BACKEND,
    DEFAULT_SVR_URL,
    resolve_mineru_backend_config,
)
from pdf_trans.errors import MinerUConfigError, PDFTransError
from pdf_trans.logging_utils import configure_logging
from pdf_trans.renderer import render_content_list_file
from pdf_trans.task_diagnostics import (
    collect_task_snapshot,
    create_diagnostic_zip,
    format_task_snapshot,
)
from pdf_trans.task_runs import CliTask, finish_cli_task, start_cli_task
from pdf_trans.web.config import WebSettings
from pdf_trans.workflow import (
    DEFAULT_DATA_DIR,
    process_pdf,
    process_translation_file,
)

LOGGER = logging.getLogger(__name__)


def _finish_task_safely(
    task: CliTask,
    status: str,
    *,
    referenced_artifacts: Sequence[Path] = (),
    error: str | None = None,
) -> bool:
    try:
        finish_cli_task(
            task,
            status,
            referenced_artifacts=referenced_artifacts,
            error=error,
        )
    except Exception as exc:
        LOGGER.error("无法更新任务清单：%s", exc)
        return False
    return True


def _print_group(title: str, counts: dict[str, int] | dict[int, int]) -> None:
    print(title)
    if not counts:
        print("  （无）")
        return
    for key in sorted(counts):
        print(f"  {key}: {counts[key]}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pdf_trans",
        description="使用 MinerU 解析 PDF、清洗并翻译 content list。",
    )
    parser.add_argument(
        "pdf_path",
        type=Path,
        nargs="?",
        help="要解析的 PDF 文件路径",
    )
    parser.add_argument(
        "--translate-only",
        type=Path,
        metavar="TRANSLATE_ONLY",
        help="只翻译已有的 normalized_content_list.json",
    )
    parser.add_argument(
        "--svr-url",
        default=DEFAULT_SVR_URL,
        help=f"MinerU API 服务地址（默认：{DEFAULT_SVR_URL}）",
    )
    parser.add_argument(
        "--mineru-backend",
        default=os.environ.get(
            "PDF_TRANS_MINERU_BACKEND",
            DEFAULT_MINERU_BACKEND,
        ),
        help="MinerU 解析后端：hybrid-engine 或 hybrid-http-client",
    )
    parser.add_argument(
        "--mineru-server-url",
        default=os.environ.get("PDF_TRANS_MINERU_SERVER_URL"),
        help="hybrid-http-client 使用的 OpenAI 兼容模型服务地址",
    )
    return parser


def build_cat_task_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pdf_trans cat_task",
        description="按 UUID 查看或打包 CLI/Web 任务诊断产物。",
    )
    parser.add_argument("task_uuid", help="任务的标准 UUID")
    parser.add_argument(
        "--zip",
        action="store_true",
        dest="create_zip",
        help="在当前目录创建诊断 ZIP",
    )
    return parser


def _cat_task_main(argv: Sequence[str]) -> int:
    args = build_cat_task_parser().parse_args(argv)
    try:
        settings = WebSettings.from_env()
        snapshot = collect_task_snapshot(
            args.task_uuid,
            cli_runs_dir=DEFAULT_DATA_DIR / "runs",
            web_data_dir=settings.data_dir,
            database_url=settings.database_url,
        )
        print(format_task_snapshot(snapshot), end="")
        if args.create_zip:
            result = create_diagnostic_zip(snapshot, Path.cwd())
            for warning in result.warnings:
                print(f"警告：{warning}")
            print(f"诊断 ZIP：{result.path}")
        return 0
    except (PDFTransError, OSError, ValueError) as exc:
        LOGGER.error("错误：%s", exc)
        return 1


def _print_translation_summary(stats, output_path: Path) -> None:
    print(f"text 对象总数：{stats.text_count}")
    print(f"本次模型调用数量：{stats.model_call_count}")
    print(f"跳过的已成功数量：{stats.skipped_success_count}")
    print(f"翻译成功数量：{stats.success_count}")
    print(f"翻译失败数量：{stats.failed_count}")
    print(f"待翻译数量：{stats.pending_count}")
    print(f"翻译文件：{Path(output_path).resolve()}")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["cat_task"]:
        configure_logging()
        return _cat_task_main(arguments[1:])

    parser = build_parser()
    args = parser.parse_args(arguments)
    if (args.pdf_path is None) == (args.translate_only is None):
        parser.error("必须且只能指定 PDF 路径或 --translate-only")

    mineru_config = None
    if args.pdf_path is not None:
        try:
            mineru_config = resolve_mineru_backend_config(
                args.mineru_backend,
                args.mineru_server_url,
            )
        except MinerUConfigError as exc:
            parser.error(str(exc))

    task_type = "translate-only" if args.translate_only is not None else "full"
    input_path = (
        args.translate_only
        if args.translate_only is not None
        else args.pdf_path
    )
    assert input_path is not None
    task = start_cli_task(
        DEFAULT_DATA_DIR / "runs",
        task_type,
        input_path,
        task_id=str(uuid.uuid4()),
    )
    print(f"任务 UUID：{task.task_id}")
    configure_logging(log_path=task.log_path)

    try:
        if args.translate_only is not None:
            result = process_translation_file(args.translate_only)
            markdown_path = result.translated_path.with_name("rendered.md")
            render_content_list_file(result.translated_path, markdown_path)
            if not _finish_task_safely(
                task,
                "succeeded",
                referenced_artifacts=(
                    args.translate_only,
                    result.translated_path,
                    markdown_path,
                ),
            ):
                return 1
            _print_translation_summary(result.stats, result.translated_path)
            print(f"Markdown 文件：{markdown_path.resolve()}")
            return 0
        result = process_pdf(
            args.pdf_path,
            svr_url=args.svr_url,
            mineru_backend=mineru_config.backend,
            mineru_server_url=mineru_config.server_url,
            data_dir=task.root,
        )
    except (PDFTransError, OSError) as exc:
        _finish_task_safely(task, "failed", error=str(exc))
        LOGGER.error("错误：%s", exc)
        return 1
    except BaseException as exc:
        _finish_task_safely(task, "failed", error=str(exc))
        raise

    if not _finish_task_safely(
        task,
        "succeeded",
        referenced_artifacts=(
            result.source_path,
            result.output_path,
            result.markdown_path,
            result.candidates_path,
            result.normalized_path,
            result.translated_path,
        ),
    ):
        return 1

    print(f"处理前数量：{result.before_count}")
    print(f"过滤数量：{result.filtered_count}")
    print(f"处理后数量：{result.after_count}")
    print(f"输出文件：{result.output_path}")
    _print_group("清洗后 type 统计：", result.content_stats.type_counts)
    print(f"带 text_level 的 text 数量：{result.content_stats.text_level_count}")
    _print_group("按 text_level 分组：", result.content_stats.text_level_counts)
    _print_group("每个 page_idx 的元素数量：", result.content_stats.page_idx_counts)
    print(f"Markdown 文件：{result.markdown_path}")
    print(f"跨页段落候选数量：{result.candidate_count}")
    print(f"跨页候选报告：{result.candidates_path}")
    print(f"规范化后数量：{result.normalized_count}")
    print(f"规范化文件：{result.normalized_path}")
    _print_translation_summary(result.translation_stats, result.translated_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
