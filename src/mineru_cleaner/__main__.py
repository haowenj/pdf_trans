from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from mineru_cleaner.client import DEFAULT_SVR_URL
from mineru_cleaner.errors import MinerUCleanerError
from mineru_cleaner.workflow import process_pdf


def _print_group(title: str, counts: dict[str, int] | dict[int, int]) -> None:
    print(title)
    if not counts:
        print("  （无）")
        return
    for key in sorted(counts):
        print(f"  {key}: {counts[key]}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m mineru_cleaner",
        description="使用 MinerU 解析 PDF 并清洗 content list。",
    )
    parser.add_argument("pdf_path", type=Path, help="要解析的 PDF 文件路径")
    parser.add_argument(
        "--svr-url",
        default=DEFAULT_SVR_URL,
        help=f"MinerU API 服务地址（默认：{DEFAULT_SVR_URL}）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = process_pdf(args.pdf_path, svr_url=args.svr_url)
    except (MinerUCleanerError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
