import json
from io import BytesIO
from zipfile import ZipFile

import pytest

from pdf_trans import archive as mineru_archive
from pdf_trans.archive import extract_zip, find_content_list
from pdf_trans.errors import ArchiveError
from pdf_trans.formula_scanner import scan_content_list


def make_zip(files: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_extract_zip_preserves_structure_and_finds_current_content_list(tmp_path):
    archive_bytes = make_zip(
        {
            "paper/hybrid_auto/paper_content_list.json": b"[]",
            "paper/hybrid_auto/paper_content_list_v2.json": b"[]",
            "paper/hybrid_auto/images/a.jpg": b"image",
        }
    )

    extracted = extract_zip(archive_bytes, tmp_path / "data")
    content_list = find_content_list(extracted)

    assert content_list == (
        tmp_path / "data/paper/hybrid_auto/paper_content_list.json"
    ).resolve()
    assert (tmp_path / "data/paper/hybrid_auto/images/a.jpg").read_bytes() == b"image"


def test_find_content_list_ignores_files_from_previous_runs(tmp_path):
    previous = tmp_path / "data/old/hybrid_auto/old_content_list.json"
    previous.parent.mkdir(parents=True)
    previous.write_text("[]", encoding="utf-8")
    archive_bytes = make_zip(
        {"new/hybrid_auto/new_content_list.json": b"[]"}
    )

    extracted = extract_zip(archive_bytes, tmp_path / "data")

    assert find_content_list(extracted).name == "new_content_list.json"


def test_resolve_content_list_converts_v1_structured_content(tmp_path):
    archive_bytes = make_zip({
        "result/structured_content.json": json.dumps({"pages": [{
            "page_idx": 2,
            "blocks": [
                {"type": "paragraph_title", "level": 2, "content": "标题", "bbox": [1, 2, 3, 4]},
                {"type": "table", "content": "<table></table>",
                 "image_source": "images/table.jpg",
                 "captions": [{"content": "表题"}], "footnotes": [{"content": "单位"}]},
                {"type": "image", "image_source": "images/seal.jpg",
                 "captions": [{"content": "印章"}]},
            ],
        }]}).encode(),
        "result/images/seal.jpg": b"image",
        "result/images/table.jpg": b"table",
    })
    extracted = extract_zip(archive_bytes, tmp_path / "data")

    content_path = mineru_archive.resolve_content_list(extracted)

    assert content_path.name == "structured_content_list.json"
    assert json.loads(content_path.read_text()) == [
        {"type": "text", "page_idx": 2, "bbox": [1, 2, 3, 4],
         "text": "标题", "text_level": 2},
        {"type": "table", "page_idx": 2, "table_body": "<table></table>",
         "table_caption": ["表题"], "table_footnote": ["单位"],
         "img_path": "images/table.jpg"},
        {"type": "image", "page_idx": 2, "image_caption": ["印章"],
         "image_footnote": [], "img_path": "images/seal.jpg"},
    ]
    assert (content_path.parent / "images/seal.jpg").read_bytes() == b"image"


def test_resolve_content_list_converts_markdown_table_for_formula_audit(tmp_path):
    archive_bytes = make_zip({
        "structured_content.json": json.dumps({"pages": [{
            "page_idx": 7,
            "blocks": [{
                "type": "table",
                "content": "| Material | Value |\n| --- | --- |\n| A \\| B | $x$ &amp; 2 |",
                "image_source": "images/table.jpg",
            }],
        }]}).encode(),
    })
    extracted = extract_zip(archive_bytes, tmp_path / "data")

    content_path = mineru_archive.resolve_content_list(extracted)
    items = json.loads(content_path.read_text())

    assert items[0]["table_body"].startswith("<table>")
    assert "A | B" in items[0]["table_body"]
    assert [formula.raw_formula for formula in scan_content_list(items)] == ["$x$"]


def test_resolve_content_list_prefers_matching_middle_json_table_html(tmp_path):
    html = '<table><tr><td rowspan="2">A</td><td>$x$</td></tr></table>'
    archive_bytes = make_zip({
        "result/structured_content.json": json.dumps({"pages": [{
            "page_idx": 7,
            "blocks": [{"type": "table", "bbox": [1, 2, 3, 4],
                        "content": "| A | $x$ |\n| --- | --- |"}],
        }]}).encode(),
        "result/middle_json.json": json.dumps({"pages": [{
            "page_idx": 7,
            "blocks": [{"type": "table", "bbox": [1, 2, 3, 4],
                        "content": [{"type": "table_body", "content": html}]}],
        }]}).encode(),
    })
    extracted = extract_zip(archive_bytes, tmp_path / "data")

    content_path = mineru_archive.resolve_content_list(extracted)
    items = json.loads(content_path.read_text())

    assert items[0]["table_body"] == html
    assert [formula.raw_formula for formula in scan_content_list(items)] == ["$x$"]


@pytest.mark.parametrize("unsafe_name", ["../escape.json", "/absolute.json"])
def test_extract_zip_rejects_unsafe_paths(tmp_path, unsafe_name):
    archive_bytes = make_zip({unsafe_name: b"unsafe"})

    with pytest.raises(ArchiveError, match="不安全"):
        extract_zip(archive_bytes, tmp_path / "data")


def test_extract_zip_rejects_reserved_output_path(tmp_path):
    archive_bytes = make_zip({"mineru_result.zip": b"collision"})

    with pytest.raises(ArchiveError, match="保留路径"):
        extract_zip(
            archive_bytes,
            tmp_path / "data",
            reserved_paths=("mineru_result.zip",),
        )


@pytest.mark.parametrize("names, expected_count", [([], 0), (["a", "b"], 2)])
def test_find_content_list_requires_exactly_one_match(
    tmp_path, names, expected_count
):
    first = tmp_path / "a_content_list.json"
    second = tmp_path / "b_content_list.json"
    paths = {"a": first, "b": second}
    for name in names:
        paths[name].write_text("[]", encoding="utf-8")

    with pytest.raises(ArchiveError, match=f"找到 {expected_count} 个"):
        find_content_list(paths[name] for name in names)
