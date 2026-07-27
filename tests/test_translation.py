import copy
import json
import logging
import threading
import time

import pytest

from pdf_trans.errors import TranslationContentError
from pdf_trans.translation import (
    TranslationStats,
    translate_content_list_file,
    write_json_atomic,
)


class FakeTranslator:
    def __init__(self, results):
        self.results = iter(results)
        self.received = []

    def translate(self, text):
        self.received.append(text)
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
        {"type": "text", "text": "Beta $x^2$", "custom": {"a": 1}},
        {"type": "text", "text": "Gamma C-1"},
    ]
    source.write_text(json.dumps(items), encoding="utf-8")
    translator = FakeTranslator(["甲 [38]", "乙 $x^2$", "丙 C-1"])

    stats = translate_content_list_file(
        source,
        output,
        translator,
        concurrency=1,
    )

    assert translator.received == ["Alpha [38]", "Beta $x^2$", "Gamma C-1"]
    translated = read_items(output)
    assert translated[0]["text"] == "Alpha [38]"
    assert translated[0]["translated_text"] == "甲 [38]"
    assert translated[0]["translation_status"] == "success"
    assert translated[1] == items[1]
    assert stats == TranslationStats(3, 3, 0, 3, 0, 0)


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
    assert stats == TranslationStats(2, 4, 0, 1, 1, 0)
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
    assert stats == TranslationStats(3, 2, 1, 3, 0, 0)
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
    assert stats == TranslationStats(3, 3, 0, 3, 0, 0)


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
