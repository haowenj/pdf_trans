from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_compose_forwards_mineru_backend_configuration():
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "PDF_TRANS_MINERU_BACKEND: ${PDF_TRANS_MINERU_BACKEND:-hybrid-engine}" in compose
    assert "PDF_TRANS_MINERU_SERVER_URL: ${PDF_TRANS_MINERU_SERVER_URL:-}" in compose


def test_compose_forwards_optional_translation_thinking_configuration():
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(
        encoding="utf-8"
    )

    assert (
        "TRANSLATION_ENABLE_THINKING: "
        "${TRANSLATION_ENABLE_THINKING:-}"
    ) in compose


def test_readme_documents_optional_translation_thinking_configuration():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "TRANSLATION_ENABLE_THINKING=false" in readme
    assert "chat_template_kwargs" in readme
    assert "未配置时不发送" in readme
    assert "GPUStack" in readme
    assert "vLLM" in readme


def test_web_image_installs_node_for_formula_audit():
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert (
        "apt-get install -y --no-install-recommends nodejs"
        in dockerfile
    )
