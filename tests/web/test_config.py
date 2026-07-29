from pathlib import Path

import pytest

from pdf_trans.errors import MinerUConfigError
from pdf_trans.web.config import WebSettings


def test_settings_use_portable_defaults(tmp_path: Path) -> None:
    settings = WebSettings.from_env(environ={}, project_root=tmp_path)

    assert settings.data_dir == (tmp_path / "data/web").resolve()
    assert settings.database_url == (
        f"sqlite:///{(tmp_path / 'data/web/pdf_trans.db').resolve()}"
    )
    assert settings.max_upload_mib == 200
    assert settings.max_upload_bytes == 200 * 1024 * 1024
    assert settings.mineru_url == "http://127.0.0.1:7100"
    assert settings.mineru_backend == "hybrid-engine"
    assert settings.mineru_server_url is None
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000


def test_settings_read_web_environment(tmp_path: Path) -> None:
    settings = WebSettings.from_env(
        environ={
            "PDF_TRANS_WEB_DATA_DIR": str(tmp_path / "runtime"),
            "PDF_TRANS_DATABASE_URL": "mysql+pymysql://u:p@db/pdf_trans",
            "PDF_TRANS_MAX_UPLOAD_MIB": "12",
            "PDF_TRANS_MINERU_URL": "http://mineru:7200/",
            "PDF_TRANS_MINERU_BACKEND": "hybrid-http-client",
            "PDF_TRANS_MINERU_SERVER_URL": "http://gpustack:8000/",
            "PDF_TRANS_WEB_HOST": "0.0.0.0",
            "PDF_TRANS_WEB_PORT": "9000",
        },
        project_root=tmp_path,
    )

    assert settings.data_dir == (tmp_path / "runtime").resolve()
    assert settings.database_url == "mysql+pymysql://u:p@db/pdf_trans"
    assert settings.max_upload_bytes == 12 * 1024 * 1024
    assert settings.mineru_url == "http://mineru:7200"
    assert settings.mineru_backend == "hybrid-http-client"
    assert settings.mineru_server_url == "http://gpustack:8000"
    assert settings.host == "0.0.0.0"
    assert settings.port == 9000


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PDF_TRANS_MAX_UPLOAD_MIB", "0"),
        ("PDF_TRANS_MAX_UPLOAD_MIB", "1.5"),
        ("PDF_TRANS_WEB_PORT", "0"),
        ("PDF_TRANS_WEB_PORT", "65536"),
    ],
)
def test_settings_reject_invalid_positive_integers(
    tmp_path: Path, name: str, value: str
) -> None:
    with pytest.raises(ValueError, match=name):
        WebSettings.from_env(
            environ={name: value},
            project_root=tmp_path,
        )


@pytest.mark.parametrize(
    "environ",
    [
        {"PDF_TRANS_MINERU_BACKEND": "pipeline"},
        {"PDF_TRANS_MINERU_BACKEND": "hybrid-http-client"},
        {
            "PDF_TRANS_MINERU_BACKEND": "hybrid-http-client",
            "PDF_TRANS_MINERU_SERVER_URL": " ",
        },
    ],
)
def test_settings_reject_invalid_mineru_configuration(
    tmp_path: Path, environ
) -> None:
    with pytest.raises(MinerUConfigError, match="PDF_TRANS_MINERU_"):
        WebSettings.from_env(environ=environ, project_root=tmp_path)
