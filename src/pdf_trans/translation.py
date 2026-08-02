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
from pdf_trans.formula_protection import (
    FormulaProtectionContext,
    FormulaProtectionError,
)
from pdf_trans.logging_utils import logged_stage
from pdf_trans.table_cell_protection import (
    CellProtectionError,
    restore_cell_segment,
)
from pdf_trans.table_translation import (
    MAX_BATCH_FORMULAS,
    MAX_BATCH_ITEMS,
    MAX_BATCH_TOKENS,
    CellFallback,
    CellWorkItem,
    TableTranslationError,
    plan_cell_batches,
    prepare_table_translation,
)
from pdf_trans.translation_client import DEFAULT_TRANSLATION_CONCURRENCY

LOGGER = logging.getLogger(__name__)
_TABLE_METADATA_FIELDS = (
    "table_translation_partial",
    "table_translation_success_cell_count",
    "table_translation_fallback_cell_count",
    "table_translation_fallbacks",
)
_AUXILIARY_STATE_FIELD = "auxiliary_translation"
_AUXILIARY_FIELD_SPECS: dict[
    str, tuple[tuple[str, str], ...]
] = {
    "table": (
        ("table_caption", "translated_table_caption"),
        ("table_footnote", "translated_table_footnote"),
    ),
    "image": (
        ("image_caption", "translated_image_caption"),
        ("image_footnote", "translated_image_footnote"),
    ),
    "chart": (
        ("chart_caption", "translated_chart_caption"),
        ("chart_footnote", "translated_chart_footnote"),
    ),
}


class TextTranslator(Protocol):
    def translate(
        self,
        text: str,
        *,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        ...


@dataclass(frozen=True)
class TranslationStats:
    text_count: int
    model_call_count: int
    skipped_success_count: int
    success_count: int
    failed_count: int
    pending_count: int
    table_count: int = 0
    table_success_count: int = 0
    table_failed_count: int = 0
    table_pending_count: int = 0
    table_partial_success_count: int = 0
    table_translation_success_cell_count: int = 0
    table_translation_fallback_cell_count: int = 0
    skipped_table_success_count: int = 0
    text_model_call_count: int = 0
    table_model_call_count: int = 0
    auxiliary_count: int = 0
    skipped_auxiliary_success_count: int = 0
    auxiliary_success_count: int = 0
    auxiliary_failed_count: int = 0
    auxiliary_pending_count: int = 0
    auxiliary_model_call_count: int = 0


def format_translation_stats(stats: TranslationStats) -> str:
    model_call_summary = (
        f"模型调用总数：{stats.model_call_count}（正文 "
        f"{stats.text_model_call_count}，表格 {stats.table_model_call_count}，"
        f"附属文本 {stats.auxiliary_model_call_count}）"
        if stats.auxiliary_count
        else (
            f"模型调用总数：{stats.model_call_count}（正文 "
            f"{stats.text_model_call_count}，表格 {stats.table_model_call_count}）"
        )
    )
    return "\n".join(
        (
            "正文翻译：",
            f"  总数：{stats.text_count}",
            f"  跳过已有成功：{stats.skipped_success_count}",
            f"  成功：{stats.success_count}",
            f"  失败：{stats.failed_count}",
            f"  pending：{stats.pending_count}",
            "表格翻译：",
            f"  总数：{stats.table_count}",
            f"  跳过已有成功：{stats.skipped_table_success_count}",
            f"  成功：{stats.table_success_count}",
            f"  其中部分成功：{stats.table_partial_success_count}",
            f"  失败：{stats.table_failed_count}",
            f"  pending：{stats.table_pending_count}",
            "  成功单元格："
            f"{stats.table_translation_success_cell_count}",
            "  回退原文单元格："
            f"{stats.table_translation_fallback_cell_count}",
            "附属文本翻译：",
            f"  总数：{stats.auxiliary_count}",
            f"  跳过已有成功：{stats.skipped_auxiliary_success_count}",
            f"  成功：{stats.auxiliary_success_count}",
            f"  失败：{stats.auxiliary_failed_count}",
            f"  pending：{stats.auxiliary_pending_count}",
            f"  模型调用：{stats.auxiliary_model_call_count}",
            model_call_summary,
        )
    )


@dataclass(frozen=True)
class TranslationOutcome:
    index: int
    section_number: int
    status: Literal["success", "failed"]
    translated_text: str | None
    error: str | None
    model_call_count: int
    elapsed_seconds: float


@dataclass(frozen=True)
class TableTranslationOutcome:
    index: int
    table_number: int
    status: Literal["success", "failed"]
    translated_table_body: str | None
    error: str | None
    model_call_count: int
    elapsed_seconds: float
    partial: bool = False
    success_cell_count: int = 0
    fallback_cell_count: int = 0
    fallbacks: tuple[CellFallback, ...] = ()


@dataclass(frozen=True)
class AuxiliaryTranslationOutcome:
    index: int
    field: str
    item_index: int
    auxiliary_number: int
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
    if normalized.get("type") == "table":
        if ("table_body" in normalized) != ("table_body" in existing):
            return False
        if normalized.get("table_body") != existing.get("table_body"):
            return False
    for source_field, _ in _AUXILIARY_FIELD_SPECS.get(
        normalized.get("type"), ()
    ):
        if (source_field in normalized) != (source_field in existing):
            return False
        if normalized.get(source_field) != existing.get(source_field):
            return False
    return True


def _auxiliary_specs(item: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return _AUXILIARY_FIELD_SPECS.get(item.get("type"), ())


def _is_non_blank_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _invalid_auxiliary_entry_state() -> dict[str, Any]:
    return {
        "translation_status": "failed",
        "translation_error": "附属文本必须是非空字符串",
    }


def _build_initial_auxiliary_values(
    raw_values: list[Any],
) -> tuple[list[str | None], list[dict[str, Any]]]:
    translated_values: list[str | None] = []
    entries: list[dict[str, Any]] = []
    for value in raw_values:
        translated_values.append(None)
        if _is_non_blank_string(value):
            entries.append(
                {
                    "translation_status": "pending",
                    "translation_error": None,
                }
            )
        else:
            entries.append(_invalid_auxiliary_entry_state())
    return translated_values, entries


def _initialize_auxiliary_state(item: dict[str, Any]) -> None:
    state: dict[str, list[dict[str, Any]]] = {}
    for source_field, translated_field in _auxiliary_specs(item):
        raw_values = item.get(source_field)
        item.pop(translated_field, None)
        if not isinstance(raw_values, list):
            continue
        translated_values, entries = _build_initial_auxiliary_values(raw_values)
        item[translated_field] = translated_values
        state[source_field] = entries
    if state:
        item[_AUXILIARY_STATE_FIELD] = state
    else:
        item.pop(_AUXILIARY_STATE_FIELD, None)


def _restore_auxiliary_state(
    item: dict[str, Any],
    old: dict[str, Any],
    index: int,
) -> None:
    specs = _auxiliary_specs(item)
    expected_fields = {source_field for source_field, _ in specs}
    old_state = old.get(_AUXILIARY_STATE_FIELD)
    if _AUXILIARY_STATE_FIELD in old and not isinstance(old_state, dict):
        raise TranslationContentError(
            f"断点文件第 {index} 个对象的附属文本状态无效"
        )
    if isinstance(old_state, dict) and not set(old_state) <= expected_fields:
        raise TranslationContentError(
            f"断点文件第 {index} 个对象包含未知附属文本状态字段"
        )

    state: dict[str, list[dict[str, Any]]] = {}
    for source_field, translated_field in specs:
        raw_values = item.get(source_field)
        translated_present = translated_field in old
        state_present = (
            isinstance(old_state, dict) and source_field in old_state
        )
        if not isinstance(raw_values, list):
            if translated_present or state_present:
                raise TranslationContentError(
                    f"断点文件第 {index} 个对象的附属文本字段结构无效"
                )
            item.pop(translated_field, None)
            continue

        if not translated_present and not state_present:
            translated_values, entries = _build_initial_auxiliary_values(
                raw_values
            )
        elif translated_present != state_present:
            raise TranslationContentError(
                f"断点文件第 {index} 个对象的附属文本状态字段不完整"
            )
        else:
            translated_values = old[translated_field]
            entries = old_state[source_field]
            if (
                not isinstance(translated_values, list)
                or len(translated_values) != len(raw_values)
                or not isinstance(entries, list)
                or len(entries) != len(raw_values)
            ):
                raise TranslationContentError(
                    f"断点文件第 {index} 个对象的附属文本数组长度不一致"
                )
            translated_values = copy.deepcopy(translated_values)
            entries = copy.deepcopy(entries)
            for item_index, (translated, entry) in enumerate(
                zip(translated_values, entries)
            ):
                if not isinstance(entry, dict) or set(entry) != {
                    "translation_status",
                    "translation_error",
                }:
                    raise TranslationContentError(
                        f"断点文件第 {index} 个对象的附属文本第 "
                        f"{item_index} 项状态无效"
                    )
                status = entry["translation_status"]
                error = entry["translation_error"]
                if status == "success":
                    valid = _is_non_blank_string(translated) and error is None
                elif status == "pending":
                    valid = translated is None and error is None
                elif status == "failed":
                    valid = (
                        translated is None and _is_non_blank_string(error)
                    )
                else:
                    valid = False
                if not valid:
                    raise TranslationContentError(
                        f"断点文件第 {index} 个对象的附属文本第 "
                        f"{item_index} 项状态与译文不一致"
                    )
        item[translated_field] = translated_values
        state[source_field] = entries

    if state:
        item[_AUXILIARY_STATE_FIELD] = state
    else:
        item.pop(_AUXILIARY_STATE_FIELD, None)


def _prepare_new_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = copy.deepcopy(items)
    for item in prepared:
        if item.get("type") == "text":
            item.pop("translated_text", None)
            item.pop("translation_error", None)
            item["translation_status"] = "pending"
        elif item.get("type") == "table":
            item.pop("translated_table_body", None)
            item.pop("translation_error", None)
            for field in _TABLE_METADATA_FIELDS:
                item.pop(field, None)
            item["translation_status"] = "pending"
        _initialize_auxiliary_state(item)
    return prepared


def _clear_table_metadata(item: dict[str, Any]) -> None:
    for field in _TABLE_METADATA_FIELDS:
        item.pop(field, None)


def _restore_table_metadata(
    item: dict[str, Any],
    old: dict[str, Any],
    index: int,
) -> None:
    present = [field in old for field in _TABLE_METADATA_FIELDS]
    if any(present) and not all(present):
        raise TranslationContentError(
            f"断点文件第 {index} 个 success 表格的单元格翻译元数据不完整"
        )
    if not any(present):
        item["table_translation_partial"] = False
        item["table_translation_success_cell_count"] = 0
        item["table_translation_fallback_cell_count"] = 0
        item["table_translation_fallbacks"] = []
        return

    partial = old["table_translation_partial"]
    success_count = old["table_translation_success_cell_count"]
    fallback_count = old["table_translation_fallback_cell_count"]
    fallbacks = old["table_translation_fallbacks"]
    valid_fallbacks = (
        isinstance(fallbacks, list)
        and len(fallbacks) == fallback_count
        and all(
            isinstance(value, dict)
            and set(value) == {"cell_id", "error"}
            and isinstance(value["cell_id"], str)
            and bool(value["cell_id"])
            and isinstance(value["error"], str)
            and bool(value["error"])
            for value in fallbacks
        )
    )
    if (
        not isinstance(partial, bool)
        or type(success_count) is not int
        or success_count < 0
        or type(fallback_count) is not int
        or fallback_count < 0
        or partial != (fallback_count > 0)
        or not valid_fallbacks
        or len({value["cell_id"] for value in fallbacks})
        != len(fallbacks)
    ):
        raise TranslationContentError(
            f"断点文件第 {index} 个 success 表格的单元格翻译元数据无效"
        )
    item["table_translation_partial"] = partial
    item["table_translation_success_cell_count"] = success_count
    item["table_translation_fallback_cell_count"] = fallback_count
    item["table_translation_fallbacks"] = copy.deepcopy(fallbacks)


def _restore_table_state(
    item: dict[str, Any],
    old: dict[str, Any],
    index: int,
) -> None:
    status = old.get("translation_status")
    if status is None:
        item.pop("translated_table_body", None)
        item.pop("translation_error", None)
        _clear_table_metadata(item)
        item["translation_status"] = "pending"
        return
    if status == "success":
        translated = old.get("translated_table_body")
        if not isinstance(translated, str) or not translated.strip():
            raise TranslationContentError(
                f"断点文件第 {index} 个 success 表格缺少有效 "
                "translated_table_body"
            )
        item["translated_table_body"] = translated
        item["translation_status"] = "success"
        item.pop("translation_error", None)
        _restore_table_metadata(item, old, index)
        return
    if status in {"pending", "failed"}:
        item.pop("translated_table_body", None)
        _clear_table_metadata(item)
        item["translation_status"] = status
        if status == "failed":
            error = old.get("translation_error")
            if not isinstance(error, str) or not error.strip():
                raise TranslationContentError(
                    f"断点文件第 {index} 个 failed 表格状态字段无效"
                )
            item["translation_error"] = error
        else:
            item.pop("translation_error", None)
        return
    raise TranslationContentError(
        f"断点文件第 {index} 个 table 对象的 translation_status 无效"
    )


def _prepare_resumed_items(
    normalized: list[dict[str, Any]], existing: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], int, int, int]:
    if len(normalized) != len(existing):
        raise TranslationContentError("断点文件与规范化内容的对象数量不一致")

    prepared: list[dict[str, Any]] = []
    skipped_success = 0
    skipped_table_success = 0
    skipped_auxiliary_success = 0
    for index, (current, old) in enumerate(zip(normalized, existing)):
        if not _same_identity(current, old):
            raise TranslationContentError(
                f"断点文件与规范化内容在第 {index} 个对象的身份字段不一致"
            )
        item = copy.deepcopy(current)
        _restore_auxiliary_state(item, old, index)
        skipped_auxiliary_success += sum(
            entry.get("translation_status") == "success"
            for entries in item.get(_AUXILIARY_STATE_FIELD, {}).values()
            for entry in entries
        )
        if current.get("type") == "table":
            _restore_table_state(item, old, index)
            if old.get("translation_status") == "success":
                skipped_table_success += 1
            prepared.append(item)
            continue
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
    return (
        prepared,
        skipped_success,
        skipped_table_success,
        skipped_auxiliary_success,
    )


def _translate_one(
    index: int,
    section_number: int,
    text: str,
    translator: TextTranslator,
    max_retries: int,
) -> TranslationOutcome:
    started = time.perf_counter()
    formula_context = FormulaProtectionContext()
    try:
        protected = formula_context.protect(text)
    except FormulaProtectionError as exc:
        elapsed = time.perf_counter() - started
        error = f"公式保护准备失败：{exc}"
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
            model_call_count=0,
            elapsed_seconds=elapsed,
        )

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
            translated = translator.translate(protected.model_text)
            if not isinstance(translated, str) or not translated.strip():
                raise ValueError("模型返回空译文")
            translated = formula_context.restore(translated, protected)
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


def _translate_auxiliary_one(
    index: int,
    field: str,
    item_index: int,
    auxiliary_number: int,
    text: str,
    translator: TextTranslator,
    max_retries: int,
) -> AuxiliaryTranslationOutcome:
    outcome = _translate_one(
        index,
        auxiliary_number,
        text,
        translator,
        max_retries,
    )
    return AuxiliaryTranslationOutcome(
        index=index,
        field=field,
        item_index=item_index,
        auxiliary_number=auxiliary_number,
        status=outcome.status,
        translated_text=outcome.translated_text,
        error=outcome.error,
        model_call_count=outcome.model_call_count,
        elapsed_seconds=outcome.elapsed_seconds,
    )


def _failed_table_outcome(
    *,
    index: int,
    table_number: int,
    error: Exception,
    model_call_count: int,
    started: float,
) -> TableTranslationOutcome:
    elapsed = time.perf_counter() - started
    message = str(error) or error.__class__.__name__
    LOGGER.error(
        "第 %d 张表翻译完成：failed，耗时 %.2f 秒，错误：%s",
        table_number,
        elapsed,
        message,
    )
    return TableTranslationOutcome(
        index=index,
        table_number=table_number,
        status="failed",
        translated_table_body=None,
        error=message,
        model_call_count=model_call_count,
        elapsed_seconds=elapsed,
    )


def _translate_table_one(
    index: int,
    table_number: int,
    table_body: Any,
    translator: TextTranslator,
    max_retries: int,
) -> TableTranslationOutcome:
    started = time.perf_counter()
    try:
        prepared = prepare_table_translation(table_body)
    except Exception as exc:
        return _failed_table_outcome(
            index=index,
            table_number=table_number,
            error=exc,
            model_call_count=0,
            started=started,
        )

    pending = {
        item.work_id: item for item in prepared.work_items
    }
    validated: dict[str, str] = {}
    last_errors: dict[str, str] = {}
    model_call_count = 0
    total_rounds = max_retries + 1

    for round_index in range(total_rounds):
        if not pending:
            break
        final_retry_round = (
            round_index > 0 and round_index == total_rounds - 1
        )
        max_items = (
            1
            if final_retry_round
            else max(1, MAX_BATCH_ITEMS // (2**round_index))
        )
        batches = plan_cell_batches(
            tuple(pending.values()),
            max_items=max_items,
            max_tokens=MAX_BATCH_TOKENS,
            max_formulas=MAX_BATCH_FORMULAS,
        )
        failed_this_round: dict[str, CellWorkItem] = {}
        for batch_number, batch in enumerate(batches, 1):
            model_call_count += 1
            LOGGER.info(
                "第 %d 张表开始单元格翻译：第 %d/%d 轮，第 %d/%d 批，"
                "%d 个 work item",
                table_number,
                round_index + 1,
                total_rounds,
                batch_number,
                len(batches),
                len(batch.items),
            )
            try:
                response = translator.translate(
                    batch.build_request(),
                    response_format=batch.build_response_format(),
                )
                if not isinstance(response, str) or not response.strip():
                    raise TableTranslationError(
                        "模型返回空表格翻译结果"
                    )
                parsed = batch.parse_response(response)
            except Exception as exc:
                message = str(exc) or exc.__class__.__name__
                for item in batch.items:
                    failed_this_round[item.work_id] = item
                    last_errors[item.work_id] = message
                LOGGER.warning(
                    "第 %d 张表第 %d 轮第 %d 批失败，%d 个 work item "
                    "进入后续处理：%s",
                    table_number,
                    round_index + 1,
                    batch_number,
                    len(batch.items),
                    message[:200],
                )
                continue

            for warning in parsed.warnings:
                LOGGER.warning(
                    "第 %d 张表第 %d 轮第 %d 批响应警告：%s",
                    table_number,
                    round_index + 1,
                    batch_number,
                    warning[:200],
                )
            batch_items = {
                item.work_id: item for item in batch.items
            }
            accepted_count = 0
            for work_id, translated_text in parsed.translations.items():
                item = batch_items[work_id]
                try:
                    restore_cell_segment(
                        translated_text,
                        prepared.segment_for(work_id),
                    )
                except CellProtectionError as exc:
                    failed_this_round[work_id] = item
                    last_errors[work_id] = str(exc)
                else:
                    validated[work_id] = translated_text
                    last_errors.pop(work_id, None)
                    accepted_count += 1
            for work_id, error in parsed.errors.items():
                failed_this_round[work_id] = batch_items[work_id]
                last_errors[work_id] = error
            LOGGER.info(
                "第 %d 张表第 %d 轮第 %d 批完成：验收 %d，失败 %d",
                table_number,
                round_index + 1,
                batch_number,
                accepted_count,
                len(batch.items) - accepted_count,
            )
        pending = failed_this_round

    for work_id in pending:
        last_errors.setdefault(work_id, "单元格翻译重试耗尽")
    try:
        rebuild = prepared.rebuild(validated, last_errors)
    except Exception as exc:
        return _failed_table_outcome(
            index=index,
            table_number=table_number,
            error=exc,
            model_call_count=model_call_count,
            started=started,
        )

    elapsed = time.perf_counter() - started
    for fallback in rebuild.fallbacks:
        LOGGER.warning(
            "第 %d 张表单元格 %s 回退 MinerU 原文：%s",
            table_number,
            fallback.cell_id,
            fallback.error[:200],
        )
    LOGGER.info(
        "第 %d 张表翻译完成：success，耗时 %.2f 秒，成功 %d 个单元格，"
        "回退 %d 个单元格，模型调用 %d 次",
        table_number,
        elapsed,
        rebuild.success_cell_count,
        rebuild.fallback_cell_count,
        model_call_count,
    )
    return TableTranslationOutcome(
        index=index,
        table_number=table_number,
        status="success",
        translated_table_body=rebuild.translated_html,
        error=None,
        model_call_count=model_call_count,
        elapsed_seconds=elapsed,
        partial=rebuild.fallback_cell_count > 0,
        success_cell_count=rebuild.success_cell_count,
        fallback_cell_count=rebuild.fallback_cell_count,
        fallbacks=rebuild.fallbacks,
    )


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


def _apply_table_outcome(
    item: dict[str, Any],
    outcome: TableTranslationOutcome,
) -> None:
    if outcome.status == "success":
        item["translated_table_body"] = outcome.translated_table_body
        item["translation_status"] = "success"
        item.pop("translation_error", None)
        item["table_translation_partial"] = outcome.partial
        item[
            "table_translation_success_cell_count"
        ] = outcome.success_cell_count
        item[
            "table_translation_fallback_cell_count"
        ] = outcome.fallback_cell_count
        item["table_translation_fallbacks"] = [
            {
                "cell_id": fallback.cell_id,
                "error": fallback.error,
            }
            for fallback in outcome.fallbacks
        ]
        return
    item.pop("translated_table_body", None)
    _clear_table_metadata(item)
    item["translation_status"] = "failed"
    item["translation_error"] = outcome.error


def _apply_auxiliary_outcome(
    item: dict[str, Any],
    outcome: AuxiliaryTranslationOutcome,
) -> None:
    translated_field = next(
        translated_field
        for source_field, translated_field in _auxiliary_specs(item)
        if source_field == outcome.field
    )
    translated_values = item[translated_field]
    entries = item[_AUXILIARY_STATE_FIELD][outcome.field]
    if outcome.status == "success":
        translated_values[outcome.item_index] = outcome.translated_text
        entries[outcome.item_index] = {
            "translation_status": "success",
            "translation_error": None,
        }
        return
    translated_values[outcome.item_index] = None
    entries[outcome.item_index] = {
        "translation_status": "failed",
        "translation_error": outcome.error,
    }


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


def _collect_table_translation_work(
    items: list[dict[str, Any]],
) -> list[tuple[int, int, Any]]:
    work: list[tuple[int, int, Any]] = []
    table_number = 0
    for index, item in enumerate(items):
        if item.get("type") != "table":
            continue
        table_number += 1
        if item.get("translation_status") == "success":
            continue
        work.append((index, table_number, item.get("table_body")))
    return work


def _collect_auxiliary_translation_work(
    items: list[dict[str, Any]],
) -> list[tuple[int, str, int, int, str]]:
    work: list[tuple[int, str, int, int, str]] = []
    auxiliary_number = 0
    for index, item in enumerate(items):
        state = item.get(_AUXILIARY_STATE_FIELD, {})
        for source_field, _ in _auxiliary_specs(item):
            raw_values = item.get(source_field)
            if not isinstance(raw_values, list):
                continue
            entries = state.get(source_field, [])
            for item_index, text in enumerate(raw_values):
                auxiliary_number += 1
                if (
                    item_index >= len(entries)
                    or entries[item_index].get("translation_status")
                    == "success"
                ):
                    continue
                if not _is_non_blank_string(text):
                    continue
                work.append(
                    (
                        index,
                        source_field,
                        item_index,
                        auxiliary_number,
                        text,
                    )
                )
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
            (
                items,
                skipped_success,
                skipped_table_success,
                skipped_auxiliary_success,
            ) = _prepare_resumed_items(normalized, existing)
        else:
            items = _prepare_new_items(normalized)
            skipped_success = 0
            skipped_table_success = 0
            skipped_auxiliary_success = 0

        writer = checkpoint_writer or write_json_atomic
        writer(output, items)
        text_count = sum(item.get("type") == "text" for item in items)
        table_count = sum(item.get("type") == "table" for item in items)
        auxiliary_count = sum(
            len(item.get(source_field))
            for item in items
            for source_field, _ in _auxiliary_specs(item)
            if isinstance(item.get(source_field), list)
        )
        checkpoint_stage.set_result(
            f"正文共 {text_count} 段，跳过已有 success {skipped_success} 段；"
            f"表格共 {table_count} 张，跳过已有 success "
            f"{skipped_table_success} 张；附属文本共 {auxiliary_count} 条，"
            f"跳过已有 success {skipped_auxiliary_success} 条"
        )

    work = _collect_translation_work(items)
    table_work = _collect_table_translation_work(items)
    auxiliary_work = _collect_auxiliary_translation_work(items)
    LOGGER.info(
        "准备并发翻译：待处理 %d 段、%d 张表、%d 条附属文本，并发数 %d",
        len(work),
        len(table_work),
        len(auxiliary_work),
        concurrency,
    )
    model_calls = 0
    text_model_calls = 0
    table_model_calls = 0
    auxiliary_model_calls = 0
    executor = ThreadPoolExecutor(
        max_workers=concurrency,
        thread_name_prefix="pdf-trans",
    )
    futures: list[Future[Any]] = [
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
    futures.extend(
        executor.submit(
            _translate_table_one,
            index,
            table_number,
            table_body,
            translator,
            max_retries,
        )
        for index, table_number, table_body in table_work
    )
    futures.extend(
        executor.submit(
            _translate_auxiliary_one,
            index,
            field,
            item_index,
            auxiliary_number,
            text,
            translator,
            max_retries,
        )
        for index, field, item_index, auxiliary_number, text in auxiliary_work
    )
    try:
        for future in as_completed(futures):
            outcome = future.result()
            if isinstance(outcome, TableTranslationOutcome):
                _apply_table_outcome(items[outcome.index], outcome)
            elif isinstance(outcome, AuxiliaryTranslationOutcome):
                _apply_auxiliary_outcome(items[outcome.index], outcome)
            else:
                _apply_outcome(items[outcome.index], outcome)
            model_calls += outcome.model_call_count
            if isinstance(outcome, TableTranslationOutcome):
                table_model_calls += outcome.model_call_count
            elif isinstance(outcome, AuxiliaryTranslationOutcome):
                auxiliary_model_calls += outcome.model_call_count
            else:
                text_model_calls += outcome.model_call_count
            writer(output, items)
    except BaseException:
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)

    if any(
        item.get("type") == "table"
        and item.get("translation_status") == "pending"
        for item in items
    ):
        raise TranslationContentError("正式翻译结束后仍存在 pending 表格")

    if any(
        entry.get("translation_status") == "pending"
        for item in items
        for source_field, _ in _auxiliary_specs(item)
        for entry in item.get(_AUXILIARY_STATE_FIELD, {}).get(source_field, [])
    ):
        raise TranslationContentError("正式翻译结束后仍存在 pending 附属文本")

    text_counts = Counter(
        item.get("translation_status")
        for item in items
        if item.get("type") == "text"
    )
    table_counts = Counter(
        item.get("translation_status")
        for item in items
        if item.get("type") == "table"
    )
    pending = text_counts.get("pending", 0)
    table_pending = table_counts.get("pending", 0)
    auxiliary_counts = Counter(
        entry.get("translation_status")
        for item in items
        for source_field, _ in _auxiliary_specs(item)
        for entry in item.get(_AUXILIARY_STATE_FIELD, {}).get(source_field, [])
    )
    if pending:
        raise TranslationContentError("正式翻译结束后仍存在 pending 对象")
    successful_tables = [
        item
        for item in items
        if (
            item.get("type") == "table"
            and item.get("translation_status") == "success"
        )
    ]
    return TranslationStats(
        text_count=text_counts.total(),
        model_call_count=model_calls,
        skipped_success_count=skipped_success,
        success_count=text_counts.get("success", 0),
        failed_count=text_counts.get("failed", 0),
        pending_count=pending,
        table_count=table_counts.total(),
        table_success_count=table_counts.get("success", 0),
        table_failed_count=table_counts.get("failed", 0),
        table_pending_count=table_pending,
        table_partial_success_count=sum(
            item.get("table_translation_partial") is True
            for item in successful_tables
        ),
        table_translation_success_cell_count=sum(
            item.get("table_translation_success_cell_count", 0)
            for item in successful_tables
        ),
        table_translation_fallback_cell_count=sum(
            item.get("table_translation_fallback_cell_count", 0)
            for item in successful_tables
        ),
        skipped_table_success_count=skipped_table_success,
        text_model_call_count=text_model_calls,
        table_model_call_count=table_model_calls,
        auxiliary_count=auxiliary_counts.total(),
        skipped_auxiliary_success_count=skipped_auxiliary_success,
        auxiliary_success_count=auxiliary_counts.get("success", 0),
        auxiliary_failed_count=auxiliary_counts.get("failed", 0),
        auxiliary_pending_count=auxiliary_counts.get("pending", 0),
        auxiliary_model_call_count=auxiliary_model_calls,
    )
