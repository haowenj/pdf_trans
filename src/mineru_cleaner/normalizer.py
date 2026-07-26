from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from mineru_cleaner.errors import NormalizationError


def _validate_candidate_edge(
    items: list[Any],
    previous_index: int,
    next_index: int,
) -> None:
    if next_index != previous_index + 1:
        raise NormalizationError(
            f"候选索引必须相邻：{previous_index} -> {next_index}"
        )

    previous = items[previous_index]
    next_item = items[next_index]
    if (
        not isinstance(previous, dict)
        or not isinstance(next_item, dict)
        or previous.get("type") != "text"
        or next_item.get("type") != "text"
    ):
        raise NormalizationError(
            f"候选对象必须都是 text：{previous_index} -> {next_index}"
        )

    previous_page_idx = previous.get("page_idx")
    next_page_idx = next_item.get("page_idx")
    if (
        type(previous_page_idx) is not int
        or type(next_page_idx) is not int
    ):
        raise NormalizationError(
            f"候选对象的 page_idx 必须是整数："
            f"{previous_index} -> {next_index}"
        )
    if next_page_idx != previous_page_idx + 1:
        raise NormalizationError(
            f"候选对象页面必须连续：{previous_index} -> {next_index}"
        )

    previous_text = previous.get("text")
    next_text = next_item.get("text")
    if (
        not isinstance(previous_text, str)
        or not previous_text.strip()
        or not isinstance(next_text, str)
        or not next_text.strip()
    ):
        raise NormalizationError(
            f"候选对象的 text 必须是非空字符串："
            f"{previous_index} -> {next_index}"
        )


def _build_candidate_chains(
    items: list[Any],
    candidates: list[dict[str, Any]],
) -> list[list[int]]:
    successors: dict[int, int] = {}
    predecessors: dict[int, int] = {}
    pairs: set[tuple[int, int]] = set()

    for position, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise NormalizationError(
                f"第 {position} 条候选必须是对象"
            )

        previous_index = candidate.get("previous_index")
        next_index = candidate.get("next_index")
        if type(previous_index) is not int or type(next_index) is not int:
            raise NormalizationError(
                f"第 {position} 条候选索引必须是整数"
            )
        if not (
            0 <= previous_index < len(items)
            and 0 <= next_index < len(items)
        ):
            raise NormalizationError(
                f"第 {position} 条候选索引越界"
            )

        pair = (previous_index, next_index)
        if pair in pairs:
            raise NormalizationError(f"候选对重复：{pair}")
        if previous_index in successors:
            raise NormalizationError(
                f"索引 {previous_index} 存在多个后继"
            )
        if next_index in predecessors:
            raise NormalizationError(
                f"索引 {next_index} 存在多个前驱"
            )

        pairs.add(pair)
        successors[previous_index] = next_index
        predecessors[next_index] = previous_index

    chains: list[list[int]] = []
    visited_pairs: set[tuple[int, int]] = set()
    starts = sorted(
        index for index in successors if index not in predecessors
    )
    for start in starts:
        chain = [start]
        current = start
        while current in successors:
            next_index = successors[current]
            pair = (current, next_index)
            if pair in visited_pairs:
                raise NormalizationError("候选形成环")
            visited_pairs.add(pair)
            chain.append(next_index)
            current = next_index
        chains.append(chain)

    if len(visited_pairs) != len(pairs):
        raise NormalizationError("候选形成环")

    for chain in chains:
        for previous_index, next_index in zip(chain, chain[1:]):
            _validate_candidate_edge(
                items,
                previous_index,
                next_index,
            )

    return chains


def normalize_cross_page_items(
    items: list[Any],
    candidates: list[dict[str, Any]],
) -> list[Any]:
    chains = _build_candidate_chains(items, candidates)
    chains_by_start = {chain[0]: chain for chain in chains}
    removed_indices = {
        index
        for chain in chains
        for index in chain[1:]
    }
    normalized: list[Any] = []

    for index, item in enumerate(items):
        chain = chains_by_start.get(index)
        if chain is not None:
            merged = copy.deepcopy(items[chain[0]])
            merged_text = items[chain[0]]["text"]
            for source_index in chain[1:]:
                merged_text = (
                    merged_text.rstrip()
                    + " "
                    + items[source_index]["text"].lstrip()
                )
            merged["text"] = merged_text
            merged["source_page_indices"] = [
                items[source_index]["page_idx"]
                for source_index in chain
            ]
            merged["source_bboxes"] = [
                copy.deepcopy(items[source_index].get("bbox"))
                for source_index in chain
            ]
            merged["merged_cross_page"] = True
            normalized.append(merged)
        elif index not in removed_indices:
            normalized.append(copy.deepcopy(item))

    return normalized


def write_normalized_content_list_file(
    items: list[Any],
    output: Path,
) -> None:
    try:
        with output.open("w", encoding="utf-8") as handle:
            json.dump(items, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except OSError as exc:
        raise NormalizationError(
            f"无法写入规范化结果：{exc}"
        ) from exc
