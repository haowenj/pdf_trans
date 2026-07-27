from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
import time
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Protocol

from pdf_trans.errors import TranslationContentError
from pdf_trans.logging_utils import logged_stage
from pdf_trans.translation_client import DEFAULT_TRANSLATION_CONCURRENCY

LOGGER = logging.getLogger(__name__)


class TextTranslator(Protocol):
    def translate(self, text: str) -> str:
        ...


@dataclass(frozen=True)
class TranslationStats:
    text_count: int
    model_call_count: int
    skipped_success_count: int
    success_count: int
    failed_count: int
    pending_count: int


@dataclass(frozen=True)
class TranslationOutcome:
    index: int
    section_number: int
    status: Literal["success", "failed"]
    translated_text: str | None
    error: str | None
    model_call_count: int
    elapsed_seconds: float


CheckpointWriter = Callable[[Path, list[dict[str, Any]]], None]


def write_json_atomic(output: Path, items: list[dict[str, Any]]) -> None:
    output = Path(output)
    temporary_path: Path | None = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(items, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, output)
        temporary_path = None
    except (OSError, TypeError, ValueError) as exc:
        raise TranslationContentError(f"无法写入翻译结果：{exc}") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _read_object_array(path: Path, description: str) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TranslationContentError(f"无法读取{description}：{exc}") from exc
    if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
        raise TranslationContentError(f"{description}的 JSON 顶层必须是对象数组")
    return raw


def _same_identity(normalized: dict[str, Any], existing: dict[str, Any]) -> bool:
    for field in ("type", "text"):
        if (field in normalized) != (field in existing):
            return False
        if normalized.get(field) != existing.get(field):
            return False
    return True


def _prepare_new_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = copy.deepcopy(items)
    for item in prepared:
        if item.get("type") == "text":
            item.pop("translated_text", None)
            item.pop("translation_error", None)
            item["translation_status"] = "pending"
    return prepared


def _prepare_resumed_items(
    normalized: list[dict[str, Any]], existing: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int]:
    if len(normalized) != len(existing):
        raise TranslationContentError("断点文件与规范化内容的对象数量不一致")

    prepared: list[dict[str, Any]] = []
    skipped_success = 0
    for index, (current, old) in enumerate(zip(normalized, existing)):
        if not _same_identity(current, old):
            raise TranslationContentError(
                f"断点文件与规范化内容在第 {index} 个对象的 type 或 text 不一致"
            )
        item = copy.deepcopy(current)
        if current.get("type") != "text":
            prepared.append(item)
            continue

        status = old.get("translation_status")
        if status == "success":
            translated = old.get("translated_text")
            if not isinstance(translated, str) or not translated.strip():
                raise TranslationContentError(
                    f"断点文件第 {index} 个 success 对象缺少有效 translated_text"
                )
            item["translated_text"] = translated
            item["translation_status"] = "success"
            item.pop("translation_error", None)
            skipped_success += 1
        elif status == "pending":
            item.pop("translated_text", None)
            item.pop("translation_error", None)
            item["translation_status"] = "pending"
        elif status == "failed":
            if old.get("translated_text") is not None or not isinstance(
                old.get("translation_error"), str
            ) or not old["translation_error"].strip():
                raise TranslationContentError(
                    f"断点文件第 {index} 个 failed 对象状态字段无效"
                )
            item["translated_text"] = None
            item["translation_status"] = "failed"
            item["translation_error"] = old["translation_error"]
        else:
            raise TranslationContentError(
                f"断点文件第 {index} 个 text 对象的 translation_status 无效"
            )
        prepared.append(item)
    return prepared, skipped_success


def _translate_one(
    index: int,
    section_number: int,
    text: str,
    translator: TextTranslator,
    max_retries: int,
) -> TranslationOutcome:
    started = time.perf_counter()
    last_error: Exception | None = None
    total_attempts = max_retries + 1
    for attempt in range(1, total_attempts + 1):
        LOGGER.info(
            "第 %d 段开始翻译：第 %d/%d 次调用",
            section_number,
            attempt,
            total_attempts,
        )
        try:
            translated = translator.translate(text)
            if not isinstance(translated, str) or not translated.strip():
                raise ValueError("模型返回空译文")
        except Exception as exc:
            last_error = exc
            if attempt < total_attempts:
                LOGGER.warning(
                    "第 %d 段第 %d 次调用失败：%s，将重试",
                    section_number,
                    attempt,
                    exc,
                )
                continue
            elapsed = time.perf_counter() - started
            error = str(exc) or exc.__class__.__name__
            LOGGER.error(
                "第 %d 段翻译完成：failed，耗时 %.2f 秒，错误：%s",
                section_number,
                elapsed,
                error,
            )
            return TranslationOutcome(
                index=index,
                section_number=section_number,
                status="failed",
                translated_text=None,
                error=error,
                model_call_count=attempt,
                elapsed_seconds=elapsed,
            )
        elapsed = time.perf_counter() - started
        LOGGER.info(
            "第 %d 段翻译完成：success，耗时 %.2f 秒，译文 %d 字符",
            section_number,
            elapsed,
            len(translated),
        )
        return TranslationOutcome(
            index=index,
            section_number=section_number,
            status="success",
            translated_text=translated,
            error=None,
            model_call_count=attempt,
            elapsed_seconds=elapsed,
        )

    raise AssertionError(f"第 {section_number} 段未产生翻译结果：{last_error}")


def _apply_outcome(
    item: dict[str, Any],
    outcome: TranslationOutcome,
) -> None:
    if outcome.status == "success":
        item["translated_text"] = outcome.translated_text
        item["translation_status"] = "success"
        item.pop("translation_error", None)
        return
    item["translated_text"] = None
    item["translation_status"] = "failed"
    item["translation_error"] = outcome.error


def _collect_translation_work(
    items: list[dict[str, Any]],
) -> list[tuple[int, int, str]]:
    work: list[tuple[int, int, str]] = []
    section_number = 0
    for index, item in enumerate(items):
        if item.get("type") != "text":
            continue
        section_number += 1
        if item.get("translation_status") == "success":
            continue
        work.append((index, section_number, item["text"]))
    return work


def translate_content_list_file(
    source: Path,
    output: Path,
    translator: TextTranslator,
    *,
    max_retries: int = 1,
    concurrency: int = DEFAULT_TRANSLATION_CONCURRENCY,
    checkpoint_writer: CheckpointWriter | None = None,
) -> TranslationStats:
    if max_retries < 0:
        raise TranslationContentError("max_retries 不能为负数")
    if type(concurrency) is not int or concurrency <= 0:
        raise TranslationContentError("concurrency 必须是大于 0 的整数")
    source = Path(source)
    output = Path(output)
    checkpoint_action = (
        "校验已有 translated_content_list.json"
        if output.exists()
        else "创建新的 translated_content_list.json"
    )
    with logged_stage(
        LOGGER,
        "准备翻译断点",
        checkpoint_action,
    ) as checkpoint_stage:
        normalized = _read_object_array(source, "规范化内容")
        if output.exists():
            if not output.is_file():
                raise TranslationContentError("断点文件不是普通文件")
            existing = _read_object_array(output, "断点翻译结果")
            items, skipped_success = _prepare_resumed_items(
                normalized,
                existing,
            )
        else:
            items = _prepare_new_items(normalized)
            skipped_success = 0

        writer = checkpoint_writer or write_json_atomic
        writer(output, items)
        text_count = sum(item.get("type") == "text" for item in items)
        checkpoint_stage.set_result(
            f"text 共 {text_count} 段，跳过已有 success {skipped_success} 段"
        )

    work = _collect_translation_work(items)
    LOGGER.info(
        "准备并发翻译：待处理 %d 段，并发数 %d",
        len(work),
        concurrency,
    )
    model_calls = 0
    executor = ThreadPoolExecutor(
        max_workers=concurrency,
        thread_name_prefix="pdf-trans",
    )
    futures: list[Future[TranslationOutcome]] = [
        executor.submit(
            _translate_one,
            index,
            section_number,
            text,
            translator,
            max_retries,
        )
        for index, section_number, text in work
    ]
    try:
        for future in as_completed(futures):
            outcome = future.result()
            _apply_outcome(items[outcome.index], outcome)
            model_calls += outcome.model_call_count
            writer(output, items)
    except BaseException:
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)

    counts = Counter(
        item.get("translation_status")
        for item in items
        if item.get("type") == "text"
    )
    pending = counts.get("pending", 0)
    if pending:
        raise TranslationContentError("正式翻译结束后仍存在 pending 对象")
    return TranslationStats(
        text_count=counts.total(),
        model_call_count=model_calls,
        skipped_success_count=skipped_success,
        success_count=counts.get("success", 0),
        failed_count=counts.get("failed", 0),
        pending_count=pending,
    )
