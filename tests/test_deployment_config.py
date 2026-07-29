from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_compose_forwards_mineru_backend_configuration():
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "PDF_TRANS_MINERU_BACKEND: ${PDF_TRANS_MINERU_BACKEND:-hybrid-engine}" in compose
    assert "PDF_TRANS_MINERU_SERVER_URL: ${PDF_TRANS_MINERU_SERVER_URL:-}" in compose
