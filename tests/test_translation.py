import copy
import json
import logging
import re
import threading
import time

import pytest

from pdf_trans.errors import TranslationContentError
from pdf_trans.table_translation import (
    plan_cell_batches,
    prepare_table_translation,
)
from pdf_trans.translation import (
    TranslationStats,
    translate_content_list_file,
    write_json_atomic,
)


FORMULA_TOKEN_RE = re.compile(r"⟪PDFTRANS_FORMULA:[^⟫]+⟫")
CELL_FORMULA_TOKEN_RE = re.compile(r"⟦M\d+⟧")


class FakeTranslator:
    def __init__(self, results):
        self.results = iter(results)
        self.received = []
        self.response_formats = []

    def translate(self, text, *, response_format=None):
        self.received.append(text)
        self.response_formats.append(response_format)
        result = next(self.results)
        if isinstance(result, BaseException):
            raise result
        return result


def read_items(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_translate_file_processes_every_text_object_and_preserves_non_text(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    items = [
        {"type": "text", "text": "Alpha [38]", "page_idx": 0},
        {"type": "image", "img_path": "images/a.png"},
        {"type": "text", "text": "Beta value", "custom": {"a": 1}},
        {"type": "text", "text": "Gamma C-1"},
    ]
    source.write_text(json.dumps(items), encoding="utf-8")
    translator = FakeTranslator(["甲 [38]", "乙值", "丙 C-1"])

    stats = translate_content_list_file(
        source,
        output,
        translator,
        concurrency=1,
    )

    assert translator.received == ["Alpha [38]", "Beta value", "Gamma C-1"]
    translated = read_items(output)
    assert translated[0]["text"] == "Alpha [38]"
    assert translated[0]["translated_text"] == "甲 [38]"
    assert translated[0]["translation_status"] == "success"
    assert translated[1] == items[1]
    assert stats == TranslationStats(
        3, 3, 0, 3, 0, 0, text_model_call_count=3
    )


class PlaceholderProbeTranslator:
    def __init__(self, *, corrupt_attempts=0):
        self.corrupt_attempts = corrupt_attempts
        self.received = []

    def translate(self, text, *, response_format=None):
        self.received.append(text)
        assert response_format is None
        if len(self.received) <= self.corrupt_attempts:
            return FORMULA_TOKEN_RE.sub("", text, count=1)
        return text.replace("Temperature", "温度").replace(
            "and density", "和密度"
        )


def test_text_translation_hides_multiple_formulas_and_restores_exactly(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    formula_one = r"${15}^{\circ}\mathrm{C},\mathrm{kg}/\mathrm{m}^{3}$"
    formula_two = "$$\n\\rho = \\frac{m}{V}\n$$"
    source_text = f"Temperature {formula_one} and density {formula_two}"
    source.write_text(
        json.dumps([{"type": "text", "text": source_text}]),
        encoding="utf-8",
    )
    translator = PlaceholderProbeTranslator()

    translate_content_list_file(source, output, translator, concurrency=1)

    assert formula_one not in translator.received[0]
    assert formula_two not in translator.received[0]
    result = read_items(output)[0]
    assert result["translated_text"] == (
        f"温度 {formula_one} 和密度 {formula_two}"
    )
    assert result["translation_status"] == "success"


def test_placeholder_validation_failure_retries_then_succeeds(tmp_path, caplog):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps([{"type": "text", "text": "Temperature $x$"}]),
        encoding="utf-8",
    )
    translator = PlaceholderProbeTranslator(corrupt_attempts=1)
    caplog.set_level(logging.INFO, logger="pdf_trans.translation")

    stats = translate_content_list_file(
        source,
        output,
        translator,
        max_retries=1,
        concurrency=1,
    )

    assert len(translator.received) == 2
    assert translator.received[0] == translator.received[1]
    assert stats.model_call_count == 2
    assert read_items(output)[0]["translation_status"] == "success"
    assert "公式占位符" in "\n".join(
        record.getMessage() for record in caplog.records
    )


def test_placeholder_validation_exhaustion_never_writes_success(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source_text = "Temperature $x^{2}$"
    source.write_text(
        json.dumps([{"type": "text", "text": source_text}]),
        encoding="utf-8",
    )

    stats = translate_content_list_file(
        source,
        output,
        PlaceholderProbeTranslator(corrupt_attempts=2),
        max_retries=1,
        concurrency=1,
    )

    result = read_items(output)[0]
    assert result["translated_text"] is None
    assert result["translation_status"] == "failed"
    assert "公式占位符" in result["translation_error"]
    assert stats == TranslationStats(
        1, 2, 0, 0, 1, 0, text_model_call_count=2
    )


def test_retries_failures_and_continues_with_model_call_count(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps([
            {"type": "text", "text": "A"},
            {"type": "text", "text": "B"},
        ]),
        encoding="utf-8",
    )
    translator = FakeTranslator(
        [RuntimeError("a1"), "甲", RuntimeError("b1"), RuntimeError("b2")]
    )

    stats = translate_content_list_file(
        source,
        output,
        translator,
        max_retries=1,
        concurrency=1,
    )

    assert translator.received == ["A", "A", "B", "B"]
    assert stats == TranslationStats(
        2, 4, 0, 1, 1, 0, text_model_call_count=4
    )
    result = read_items(output)
    assert result[0]["translation_status"] == "success"
    assert result[1]["translated_text"] is None
    assert result[1]["translation_error"] == "b2"


def test_resume_skips_success_and_retries_pending_and_failed(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    normalized = [
        {"type": "text", "text": "A", "extra": "fresh"},
        {"type": "text", "text": "B"},
        {"type": "table", "rows": [[1]]},
        {"type": "text", "text": "C"},
    ]
    source.write_text(json.dumps(normalized), encoding="utf-8")
    output.write_text(
        json.dumps([
            {**normalized[0], "translated_text": "旧甲", "translation_status": "success"},
            {**normalized[1], "translation_status": "pending", "translated_text": "旧乙"},
            normalized[2],
            {
                **normalized[3],
                "translated_text": None,
                "translation_status": "failed",
                "translation_error": "old error",
            },
        ]),
        encoding="utf-8",
    )
    translator = FakeTranslator(["新乙", "新丙"])

    stats = translate_content_list_file(
        source,
        output,
        translator,
        concurrency=1,
    )

    assert translator.received == ["B", "C"]
    assert stats == TranslationStats(
        3,
        2,
        1,
        3,
        0,
        0,
        table_count=1,
        table_failed_count=1,
        text_model_call_count=2,
    )
    result = read_items(output)
    assert result[0]["translated_text"] == "旧甲"
    assert result[1]["translated_text"] == "新乙"
    assert result[3]["translated_text"] == "新丙"
    assert "translation_error" not in result[1]
    assert "translation_error" not in result[3]


def test_interruption_leaves_checkpoint_and_rerun_skips_completed(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(json.dumps([{"type": "text", "text": "A"}, {"type": "text", "text": "B"}]), encoding="utf-8")
    first = FakeTranslator(["甲", KeyboardInterrupt()])

    with pytest.raises(KeyboardInterrupt):
        translate_content_list_file(
            source,
            output,
            first,
            concurrency=1,
        )

    assert read_items(output)[0]["translation_status"] == "success"
    second = FakeTranslator(["乙"])
    stats = translate_content_list_file(
        source,
        output,
        second,
        concurrency=1,
    )
    assert second.received == ["B"]
    assert stats.skipped_success_count == 1
    assert stats.success_count == 2


@pytest.mark.parametrize(
    "existing",
    [
        [{"type": "text", "text": "A"}],
        [{"type": "image", "text": "A"}, {"type": "text", "text": "B"}],
        [{"type": "text", "text": "stale"}, {"type": "text", "text": "B"}],
    ],
)
def test_resume_rejects_identity_mismatch_without_overwriting(tmp_path, existing):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(json.dumps([{"type": "text", "text": "A"}, {"type": "text", "text": "B"}]), encoding="utf-8")
    marker = {"marker": True}
    output.write_text(json.dumps(marker), encoding="utf-8")
    if existing:
        output.write_text(json.dumps(existing), encoding="utf-8")
    before = output.read_text(encoding="utf-8")

    with pytest.raises(TranslationContentError):
        translate_content_list_file(
            source,
            output,
            FakeTranslator([]),
            concurrency=1,
        )

    assert output.read_text(encoding="utf-8") == before


def test_resume_rejects_invalid_translation_state(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(json.dumps([{"type": "text", "text": "A"}]), encoding="utf-8")
    output.write_text(
        json.dumps([{"type": "text", "text": "A", "translation_status": "unknown"}]),
        encoding="utf-8",
    )

    with pytest.raises(TranslationContentError, match="translation_status"):
        translate_content_list_file(
            source,
            output,
            FakeTranslator([]),
            concurrency=1,
        )


def test_custom_checkpoint_writer_receives_each_completed_state(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(json.dumps([{"type": "text", "text": "A"}, {"type": "text", "text": "B"}]), encoding="utf-8")
    checkpoints = []

    def writer(path, items):
        checkpoints.append((path, copy.deepcopy(items)))
        path.write_text(json.dumps(items), encoding="utf-8")

    translate_content_list_file(
        source,
        output,
        FakeTranslator(["甲", "乙"]),
        concurrency=1,
        checkpoint_writer=writer,
    )

    assert len(checkpoints) == 3
    assert checkpoints[0][1][0]["translation_status"] == "pending"
    assert checkpoints[1][1][0]["translation_status"] == "success"
    assert checkpoints[2][1][1]["translation_status"] == "success"


class ConcurrentProbeTranslator:
    def __init__(self, participants):
        self.barrier = threading.Barrier(participants)
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def translate(self, text):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.barrier.wait(timeout=2)
            return f"译文：{text}"
        finally:
            with self.lock:
                self.active -= 1


def test_translate_file_runs_up_to_configured_concurrency(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps(
            [{"type": "text", "text": value} for value in ("A", "B", "C")]
        ),
        encoding="utf-8",
    )
    translator = ConcurrentProbeTranslator(participants=3)

    stats = translate_content_list_file(
        source,
        output,
        translator,
        concurrency=3,
    )

    assert translator.max_active == 3
    assert stats == TranslationStats(
        3, 3, 0, 3, 0, 0, text_model_call_count=3
    )


class DelayedTranslator:
    def translate(self, text):
        delay = {"slow": 0.05, "fast": 0.01}[text]
        time.sleep(delay)
        return f"译文：{text}"


def test_out_of_order_completion_preserves_array_order(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps(
            [
                {"type": "text", "text": "slow"},
                {"type": "image", "img_path": "a.png"},
                {"type": "text", "text": "fast"},
            ]
        ),
        encoding="utf-8",
    )

    stats = translate_content_list_file(
        source,
        output,
        DelayedTranslator(),
        concurrency=2,
    )

    result = read_items(output)
    assert [item["type"] for item in result] == ["text", "image", "text"]
    assert result[0]["translated_text"] == "译文：slow"
    assert result[1] == {"type": "image", "img_path": "a.png"}
    assert result[2]["translated_text"] == "译文：fast"
    assert stats.model_call_count == 2


def test_checkpoint_writer_runs_only_on_main_thread(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps(
            [{"type": "text", "text": value} for value in ("A", "B", "C")]
        ),
        encoding="utf-8",
    )
    main_thread = threading.get_ident()
    writer_threads = []
    checkpoints = []

    def writer(path, items):
        writer_threads.append(threading.get_ident())
        checkpoints.append(copy.deepcopy(items))
        path.write_text(json.dumps(items), encoding="utf-8")

    translate_content_list_file(
        source,
        output,
        ConcurrentProbeTranslator(participants=3),
        concurrency=3,
        checkpoint_writer=writer,
    )

    assert writer_threads == [main_thread] * 4
    assert len(checkpoints) == 4
    assert all(
        item.get("translation_status") == "pending"
        for item in checkpoints[0]
    )
    assert all(
        item.get("translation_status") == "success"
        for item in checkpoints[-1]
    )


def test_translation_logs_attempt_retry_and_safe_success_summary(
    tmp_path,
    caplog,
):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source_text = "sensitive source paragraph"
    translated_text = "保密的完整译文"
    source.write_text(
        json.dumps([{"type": "text", "text": source_text}]),
        encoding="utf-8",
    )
    caplog.set_level(logging.INFO, logger="pdf_trans.translation")

    translate_content_list_file(
        source,
        output,
        FakeTranslator([RuntimeError("timeout"), translated_text]),
        max_retries=1,
        concurrency=1,
    )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "第 1 段开始翻译：第 1/2 次调用" in messages
    assert "第 1 段第 1 次调用失败：timeout，将重试" in messages
    assert "第 1 段翻译完成：success" in messages
    assert f"译文 {len(translated_text)} 字符" in messages
    assert source_text not in messages
    assert translated_text not in messages


def test_write_json_atomic_uses_replace(tmp_path, monkeypatch):
    output = tmp_path / "translated_content_list.json"
    replaced = []
    original_replace = __import__("os").replace

    def replace(src, dst):
        replaced.append((src, dst))
        original_replace(src, dst)

    monkeypatch.setattr("pdf_trans.translation.os.replace", replace)
    write_json_atomic(output, [{"type": "image"}])

    assert replaced and replaced[0][1] == output
    assert read_items(output) == [{"type": "image"}]


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{", "无法读取规范化内容"),
        ('{"type": "text"}', "JSON 顶层必须是对象数组"),
        ('[{"type": "text"}, 1]', "JSON 顶层必须是对象数组"),
    ],
)
def test_translate_content_list_file_rejects_invalid_input(tmp_path, content, message):
    source = tmp_path / "normalized_content_list.json"
    source.write_text(content, encoding="utf-8")

    with pytest.raises(TranslationContentError, match=message):
        translate_content_list_file(
            source,
            tmp_path / "translated_content_list.json",
            FakeTranslator([]),
            concurrency=1,
        )


class JsonTableTranslator:
    def __init__(self, translations):
        self.translations = translations
        self.received = []
        self.response_formats = []

    def translate(self, text, *, response_format=None):
        self.received.append(text)
        self.response_formats.append(response_format)
        payload = json.loads(text)
        return json.dumps(
            {
                "cells": [
                    {
                        "cell_id": item["cell_id"],
                        "translated_text": self.translations[item["text"]],
                    }
                    for item in reversed(payload["cells"])
                ]
            },
            ensure_ascii=False,
        )


def test_translate_file_batches_table_nodes_by_id_and_preserves_source_html(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    table_body = (
        '<table><tr><th rowspan="2">Component</th>'
        '<th colspan="2">Mass Fraction</th></tr>'
        "<tr><td>Ethanol</td><td>100</td></tr></table>"
    )
    source.write_text(
        json.dumps([{"type": "table", "table_body": table_body}]),
        encoding="utf-8",
    )
    translator = JsonTableTranslator(
        {
            "Component": "组分",
            "Mass Fraction": "质量分数",
            "Ethanol": "乙醇",
        }
    )

    stats = translate_content_list_file(
        source,
        output,
        translator,
        max_retries=0,
        concurrency=1,
    )

    assert len(translator.received) == 1
    request = json.loads(translator.received[0])
    assert [item["text"] for item in request["cells"]] == [
        "Component",
        "Mass Fraction",
        "Ethanol",
    ]
    prepared = prepare_table_translation(table_body)
    assert translator.response_formats == [
        plan_cell_batches(prepared.work_items)[0].build_response_format()
    ]
    result = read_items(output)[0]
    assert result["table_body"] == table_body
    assert result["translated_table_body"] == (
        '<table><tr><th rowspan="2">组分</th>'
        '<th colspan="2">质量分数</th></tr>'
        "<tr><td>乙醇</td><td>100</td></tr></table>"
    )
    assert result["translation_status"] == "success"
    assert result["table_translation_partial"] is False
    assert result["table_translation_success_cell_count"] == 3
    assert result["table_translation_fallback_cell_count"] == 0
    assert result["table_translation_fallbacks"] == []
    assert stats == TranslationStats(
        0,
        1,
        0,
        0,
        0,
        0,
        table_count=1,
        table_success_count=1,
        table_translation_success_cell_count=3,
        table_model_call_count=1,
    )


class CorruptingTableFormulaTranslator:
    def __init__(self):
        self.received = []

    def translate(self, text, *, response_format=None):
        self.received.append(text)
        assert response_format is not None
        payload = json.loads(text)
        return json.dumps(
            {
                "cells": [
                    {
                        "cell_id": item["cell_id"],
                        "translated_text": CELL_FORMULA_TOKEN_RE.sub(
                            "", item["text"], count=1
                        ),
                    }
                    for item in payload["cells"]
                ]
            }
        )


def test_table_formula_validation_exhaustion_falls_back_only_cell(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    table_body = (
        "<table><tr><td>"
        r"Temperature ${15}^{\circ}\mathrm{C},\mathrm{kg}/\mathrm{m}^{3}$"
        "</td></tr></table>"
    )
    source.write_text(
        json.dumps([{"type": "table", "table_body": table_body}]),
        encoding="utf-8",
    )
    translator = CorruptingTableFormulaTranslator()

    translate_content_list_file(
        source,
        output,
        translator,
        max_retries=1,
        concurrency=1,
    )

    assert len(translator.received) == 2
    result = read_items(output)[0]
    assert result["table_body"] == table_body
    assert result["translated_table_body"] == table_body
    assert result["translation_status"] == "success"
    assert result["table_translation_partial"] is True
    assert result["table_translation_success_cell_count"] == 0
    assert result["table_translation_fallback_cell_count"] == 1
    assert result["table_translation_fallbacks"][0]["cell_id"] == "cell-0001"
    assert "公式占位符" in result["table_translation_fallbacks"][0]["error"]


def test_numeric_only_table_makes_no_model_call_and_finishes_successfully(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    table_body = "<table><tr><td>123</td><td>45.6</td></tr></table>"
    source.write_text(
        json.dumps([{"type": "table", "table_body": table_body}]),
        encoding="utf-8",
    )

    stats = translate_content_list_file(
        source,
        output,
        FakeTranslator([]),
        concurrency=1,
    )

    result = read_items(output)[0]
    assert result["translation_status"] == "success"
    assert result["translated_table_body"] == table_body
    assert result["table_translation_partial"] is False
    assert result["table_translation_success_cell_count"] == 0
    assert result["table_translation_fallback_cell_count"] == 0
    assert result["table_translation_fallbacks"] == []
    assert stats.model_call_count == 0


def test_reserved_marker_source_cell_falls_back_without_model_call(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    table_body = (
        "<table><tr><td>Literal ⟦M0⟧ marker</td></tr></table>"
    )
    source.write_text(
        json.dumps([{"type": "table", "table_body": table_body}]),
        encoding="utf-8",
    )
    translator = FakeTranslator([])

    translate_content_list_file(
        source,
        output,
        translator,
        concurrency=1,
    )
    result = read_items(output)[0]

    assert translator.received == []
    assert result["translation_status"] == "success"
    assert result["translated_table_body"] == table_body
    assert result["table_translation_partial"] is True
    assert result["table_translation_fallback_cell_count"] == 1
    assert result["table_translation_fallbacks"][0]["cell_id"] == "cell-0001"


class SelectiveTranslator:
    def __init__(self):
        self.received = []

    def translate(self, text, *, response_format=None):
        self.received.append(text)
        if text == "Paragraph":
            assert response_format is None
            return "正文"
        assert response_format is not None
        raise RuntimeError("table service unavailable")


def test_table_translation_exception_falls_back_and_document_continues(
    tmp_path,
    caplog,
):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    table_body = "<table><tr><td>Alpha</td></tr></table>"
    source.write_text(
        json.dumps(
            [
                {"type": "text", "text": "Paragraph"},
                {"type": "table", "table_body": table_body},
            ]
        ),
        encoding="utf-8",
    )
    caplog.set_level(logging.INFO, logger="pdf_trans.translation")

    stats = translate_content_list_file(
        source,
        output,
        SelectiveTranslator(),
        max_retries=0,
        concurrency=1,
    )

    result = read_items(output)
    assert result[0]["translated_text"] == "正文"
    assert result[0]["translation_status"] == "success"
    assert result[1]["table_body"] == table_body
    assert result[1]["translated_table_body"] == table_body
    assert result[1]["translation_status"] == "success"
    assert result[1]["table_translation_partial"] is True
    assert result[1]["table_translation_fallback_cell_count"] == 1
    assert result[1]["table_translation_fallbacks"] == [
        {"cell_id": "cell-0001", "error": "table service unavailable"}
    ]
    assert stats == TranslationStats(
        1,
        2,
        0,
        1,
        0,
        0,
        table_count=1,
        table_success_count=1,
        table_partial_success_count=1,
        table_translation_fallback_cell_count=1,
        text_model_call_count=1,
        table_model_call_count=1,
    )
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "第 1 张表翻译完成：success" in messages
    assert "回退 1 个单元格" in messages
    assert table_body not in messages


@pytest.mark.parametrize(
    "table_body",
    [
        "<table><tr><td>Alpha</tr></table>",
        "<table><tr><td>Alpha</td></tr>",
    ],
)
def test_invalid_table_html_falls_back_without_model_call(tmp_path, table_body):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps([{"type": "table", "table_body": table_body}]),
        encoding="utf-8",
    )
    translator = FakeTranslator([])

    translate_content_list_file(
        source,
        output,
        translator,
        concurrency=1,
    )

    result = read_items(output)[0]
    assert translator.received == []
    assert result["table_body"] == table_body
    assert result["translation_status"] == "failed"
    assert "HTML" in result["translation_error"]


def test_resume_skips_successful_table_by_original_table_body(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    table_body = "<table><tr><td>Alpha</td></tr></table>"
    translated_table_body = "<table><tr><td>阿尔法</td></tr></table>"
    normalized = [{"type": "table", "table_body": table_body}]
    source.write_text(json.dumps(normalized), encoding="utf-8")
    output.write_text(
        json.dumps(
            [
                {
                    **normalized[0],
                    "translated_table_body": translated_table_body,
                    "translation_status": "success",
                }
            ]
        ),
        encoding="utf-8",
    )
    translator = FakeTranslator([])

    stats = translate_content_list_file(
        source,
        output,
        translator,
        concurrency=1,
    )

    assert translator.received == []
    resumed = read_items(output)[0]
    assert resumed["translated_table_body"] == translated_table_body
    assert resumed["table_translation_partial"] is False
    assert resumed["table_translation_success_cell_count"] == 0
    assert resumed["table_translation_fallback_cell_count"] == 0
    assert resumed["table_translation_fallbacks"] == []
    assert stats == TranslationStats(
        0,
        0,
        0,
        0,
        0,
        0,
        table_count=1,
        table_success_count=1,
        skipped_table_success_count=1,
    )


@pytest.mark.parametrize(
    "response_value",
    [
        json.dumps({"cells": []}),
        json.dumps(
            {
                "cells": [
                    {
                        "cell_id": "cell-9999",
                        "translated_text": "错误 ID",
                    }
                ]
            }
        ),
        "not json",
    ],
)
def test_invalid_table_translation_response_falls_back(
    tmp_path,
    response_value,
):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    table_body = "<table><tr><td>Alpha</td></tr></table>"
    source.write_text(
        json.dumps([{"type": "table", "table_body": table_body}]),
        encoding="utf-8",
    )

    translate_content_list_file(
        source,
        output,
        FakeTranslator([response_value]),
        max_retries=0,
        concurrency=1,
    )

    result = read_items(output)[0]
    assert result["table_body"] == table_body
    assert result["translated_table_body"] == table_body
    assert result["translation_status"] == "success"
    assert result["table_translation_partial"] is True
    assert result["table_translation_fallback_cell_count"] == 1
    assert result["table_translation_fallbacks"][0]["error"]


class CellBatchTranslator:
    def __init__(self, responder):
        self.responder = responder
        self.requests = []
        self.response_formats = []

    def translate(self, text, *, response_format=None):
        payload = json.loads(text)
        self.requests.append(payload)
        self.response_formats.append(response_format)
        return self.responder(payload, len(self.requests))


def cell_response(*items):
    return json.dumps(
        {
            "cells": [
                {
                    "cell_id": cell_id,
                    "translated_text": translated,
                }
                for cell_id, translated in items
            ]
        },
        ensure_ascii=False,
    )


def test_stats_aggregate_text_and_table_successes_and_model_calls(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    table_body = "<table><tr><td>Alpha</td><td>Beta</td></tr></table>"
    source.write_text(
        json.dumps([
            {"type": "text", "text": "Paragraph"},
            {"type": "table", "table_body": table_body},
        ]),
        encoding="utf-8",
    )

    class Translator:
        def translate(self, text, *, response_format=None):
            if response_format is None:
                return "正文译文"
            payload = json.loads(text)
            return cell_response(*[
                (cell["cell_id"], cell["text"] + "译文")
                for cell in payload["cells"]
            ])

    stats = translate_content_list_file(
        source, output, Translator(), max_retries=0, concurrency=1
    )

    assert stats.text_count == 1
    assert stats.success_count == 1
    assert stats.failed_count == 0
    assert stats.pending_count == 0
    assert stats.table_count == 1
    assert stats.table_success_count == 1
    assert stats.table_failed_count == 0
    assert stats.table_pending_count == 0
    assert stats.table_partial_success_count == 0
    assert stats.table_translation_success_cell_count == 2
    assert stats.table_translation_fallback_cell_count == 0
    assert stats.model_call_count == 2
    assert stats.text_model_call_count == 1
    assert stats.table_model_call_count == 1


def test_stats_count_failed_table_without_converting_it_to_text_failure(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps([{
            "type": "table",
            "table_body": "<table><tr><td>broken</tr></table>",
        }]),
        encoding="utf-8",
    )

    stats = translate_content_list_file(
        source, output, FakeTranslator([]), max_retries=0, concurrency=1
    )

    assert stats.text_count == 0
    assert stats.success_count == 0
    assert stats.failed_count == 0
    assert stats.table_count == 1
    assert stats.table_success_count == 0
    assert stats.table_failed_count == 1
    assert stats.table_pending_count == 0
    assert stats.model_call_count == 0


def test_stats_sum_partial_table_fallbacks_across_multiple_tables(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    table_bodies = [
        "<table><tr><td>Alpha</td><td>Beta</td></tr></table>",
        "<table><tr><td>Gamma</td><td>Delta</td></tr></table>",
    ]
    source.write_text(
        json.dumps([
            {"type": "table", "table_body": body}
            for body in table_bodies
        ]),
        encoding="utf-8",
    )

    def respond(payload, call_number):
        cells = payload["cells"]
        if call_number == 1:
            return cell_response((cells[0]["cell_id"], "已译"))
        return cell_response(*[
            (cell["cell_id"], cell["text"] + "译文")
            for cell in cells
        ])

    stats = translate_content_list_file(
        source,
        output,
        CellBatchTranslator(respond),
        max_retries=0,
        concurrency=1,
    )

    assert stats.table_count == 2
    assert stats.table_success_count == 2
    assert stats.table_partial_success_count == 1
    assert stats.table_failed_count == 0
    assert stats.table_translation_success_cell_count == 3
    assert stats.table_translation_fallback_cell_count == 1


def test_resume_counts_skipped_successful_text_and_table_separately(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    table_body = "<table><tr><td>Alpha</td></tr></table>"
    normalized = [
        {"type": "text", "text": "正文"},
        {"type": "table", "table_body": table_body},
    ]
    source.write_text(json.dumps(normalized), encoding="utf-8")
    output.write_text(json.dumps([
        {
            **normalized[0],
            "translated_text": "旧正文",
            "translation_status": "success",
        },
        {
            **normalized[1],
            "translated_table_body": table_body,
            "translation_status": "success",
        },
    ]), encoding="utf-8")

    stats = translate_content_list_file(
        source, output, FakeTranslator([]), max_retries=0, concurrency=1
    )

    assert stats.skipped_success_count == 1
    assert stats.skipped_table_success_count == 1
    assert stats.success_count == 1
    assert stats.table_success_count == 1
    assert stats.model_call_count == 0


def test_pending_table_still_raises_after_translation(tmp_path, monkeypatch):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps([{
            "type": "table",
            "table_body": "<table><tr><td>Alpha</td></tr></table>",
        }]),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "pdf_trans.translation._collect_table_translation_work",
        lambda items: [],
    )

    with pytest.raises(TranslationContentError, match="pending 表格"):
        translate_content_list_file(
            source, output, FakeTranslator([]), max_retries=0, concurrency=1
        )


def test_table_retries_only_failed_cell_and_keeps_successful_formula_cell(
    tmp_path,
):
    formula = r"${15}^{\circ}\mathrm{C},\mathrm{kg}/\mathrm{m}^{3}$"
    table_body = (
        "<table><tr>"
        f"<td>Density {formula}</td>"
        "<td>Corrosion $x$ at $y$</td>"
        "</tr></table>"
    )
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps([{"type": "table", "table_body": table_body}]),
        encoding="utf-8",
    )

    def respond(payload, call_number):
        cells = payload["cells"]
        if call_number == 1:
            return cell_response(
                (cells[0]["cell_id"], "密度 ⟦M0⟧"),
                (cells[1]["cell_id"], "腐蚀 ⟦M0⟧"),
            )
        assert [cell["cell_id"] for cell in cells] == ["cell-0002"]
        return cell_response(
            ("cell-0002", "腐蚀 ⟦M0⟧ 于 ⟦M1⟧")
        )

    translator = CellBatchTranslator(respond)
    stats = translate_content_list_file(
        source,
        output,
        translator,
        max_retries=1,
        concurrency=1,
    )
    item = read_items(output)[0]

    assert len(translator.requests) == 2
    assert formula in item["translated_table_body"]
    assert "密度" in item["translated_table_body"]
    assert "腐蚀 $x$ 于 $y$" in item["translated_table_body"]
    assert item["translation_status"] == "success"
    assert item["table_translation_partial"] is False
    assert item["table_translation_success_cell_count"] == 2
    assert item["table_translation_fallback_cell_count"] == 0
    assert item["table_translation_fallbacks"] == []
    assert stats.model_call_count == 2


def test_final_failed_cell_falls_back_without_failing_the_table(tmp_path):
    table_body = (
        "<table><tr><td>Alpha $x$</td><td>Beta $y$</td></tr></table>"
    )
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps([{"type": "table", "table_body": table_body}]),
        encoding="utf-8",
    )

    def respond(payload, call_number):
        cells = payload["cells"]
        if call_number == 1:
            return cell_response(
                (cells[0]["cell_id"], "甲 ⟦M0⟧"),
                (cells[1]["cell_id"], "乙"),
            )
        assert [cell["cell_id"] for cell in cells] == ["cell-0002"]
        return cell_response(("cell-0002", "仍然缺少公式"))

    translator = CellBatchTranslator(respond)
    translate_content_list_file(
        source,
        output,
        translator,
        max_retries=1,
        concurrency=1,
    )
    item = read_items(output)[0]

    assert "甲 $x$" in item["translated_table_body"]
    assert "Beta $y$" in item["translated_table_body"]
    assert item["translation_status"] == "success"
    assert item["table_translation_partial"] is True
    assert item["table_translation_success_cell_count"] == 1
    assert item["table_translation_fallback_cell_count"] == 1
    assert item["table_translation_fallbacks"][0]["cell_id"] == "cell-0002"
    assert "缺失" in item["table_translation_fallbacks"][0]["error"]


def test_resume_preserves_partial_table_metadata(tmp_path):
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    table_body = "<table><tr><td>Alpha</td></tr></table>"
    source.write_text(
        json.dumps([{"type": "table", "table_body": table_body}]),
        encoding="utf-8",
    )
    output.write_text(
        json.dumps(
            [
                {
                    "type": "table",
                    "table_body": table_body,
                    "translated_table_body": table_body,
                    "translation_status": "success",
                    "table_translation_partial": True,
                    "table_translation_success_cell_count": 0,
                    "table_translation_fallback_cell_count": 1,
                    "table_translation_fallbacks": [
                        {
                            "cell_id": "cell-0001",
                            "error": "公式占位符存在缺失 ID",
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    translator = CellBatchTranslator(
        lambda payload, call_number: cell_response()
    )

    translate_content_list_file(
        source,
        output,
        translator,
        concurrency=1,
    )
    item = read_items(output)[0]

    assert translator.requests == []
    assert item["table_translation_partial"] is True
    assert item["table_translation_fallback_cell_count"] == 1
    assert item["table_translation_fallbacks"][0]["cell_id"] == "cell-0001"


def test_long_table_keeps_density_translation_when_later_formula_cell_fails(
    tmp_path,
):
    density_formula = (
        r"${15}^{\circ}\mathrm{C},\mathrm{kg}/\mathrm{m}^{3}$"
    )
    rows = [
        f"<tr><td>Density at {density_formula}</td>"
        "<td>775 to 840</td></tr>"
    ]
    rows.extend(
        f"<tr><td>Property {number}</td><td>{number}</td></tr>"
        for number in range(1, 40)
    )
    corrosion_source = (
        "CORROSION Copper strip, "
        r"$2\mathrm{\;h}$ at ${100}^{ \circ }\mathrm{C}$ "
        r"THERMAL STABILITY ${}^{\mathrm{v}}$"
    )
    rows.append(
        f"<tr><td>{corrosion_source}</td><td>42</td></tr>"
    )
    table_body = "<table>" + "".join(rows) + "</table>"
    source = tmp_path / "normalized_content_list.json"
    output = tmp_path / "translated_content_list.json"
    source.write_text(
        json.dumps([{"type": "table", "table_body": table_body}]),
        encoding="utf-8",
    )

    def respond(payload, call_number):
        translated = []
        for cell in payload["cells"]:
            text = cell["text"]
            if "CORROSION" in text:
                value = "腐蚀 ⟦M0⟧"
            elif "Density" in text:
                value = "密度在 ⟦M0⟧ 时"
            else:
                value = text.replace("Property", "性质")
            translated.append((cell["cell_id"], value))
        return cell_response(*translated)

    translator = CellBatchTranslator(respond)
    translate_content_list_file(
        source,
        output,
        translator,
        max_retries=1,
        concurrency=1,
    )
    item = read_items(output)[0]

    assert item["translation_status"] == "success"
    assert item["table_translation_partial"] is True
    assert (
        f"密度在 {density_formula} 时"
        in item["translated_table_body"]
    )
    assert corrosion_source in item["translated_table_body"]
    assert item["table_translation_fallback_cell_count"] == 1
    assert len(translator.requests) >= 2
    assert all(
        len(request["cells"]) <= 12
        for request in translator.requests
    )
