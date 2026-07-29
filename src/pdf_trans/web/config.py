from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pdf_trans.client import (
    DEFAULT_MINERU_BACKEND,
    resolve_mineru_backend_config,
)

DEFAULT_MINERU_URL = "http://127.0.0.1:7100"


def _positive_int(
    environ: Mapping[str, str],
    name: str,
    default: int,
    *,
    maximum: int | None = None,
) -> int:
    raw = environ.get(name, str(default))
    if not raw.isdigit():
        raise ValueError(f"{name} must be a positive integer")

    value = int(raw)
    if value < 1 or (maximum is not None and value > maximum):
        raise ValueError(f"{name} must be a positive integer")
    return value


@dataclass(frozen=True)
class WebSettings:
    data_dir: Path
    database_url: str
    max_upload_mib: int
    mineru_url: str
    mineru_backend: str
    mineru_server_url: str | None
    host: str
    port: int

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mib * 1024 * 1024

    @classmethod
    def from_env(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        project_root: Path | None = None,
    ) -> WebSettings:
        values = os.environ if environ is None else environ
        root = (
            Path(__file__).resolve().parents[3]
            if project_root is None
            else project_root.resolve()
        )
        data_dir = Path(
            values.get("PDF_TRANS_WEB_DATA_DIR", str(root / "data/web"))
        ).expanduser().resolve()
        database_url = values.get(
            "PDF_TRANS_DATABASE_URL",
            f"sqlite:///{(data_dir / 'pdf_trans.db').resolve()}",
        )
        mineru_config = resolve_mineru_backend_config(
            values.get(
                "PDF_TRANS_MINERU_BACKEND",
                DEFAULT_MINERU_BACKEND,
            ),
            values.get("PDF_TRANS_MINERU_SERVER_URL"),
        )
        return cls(
            data_dir=data_dir,
            database_url=database_url,
            max_upload_mib=_positive_int(
                values, "PDF_TRANS_MAX_UPLOAD_MIB", 200
            ),
            mineru_url=values.get(
                "PDF_TRANS_MINERU_URL", DEFAULT_MINERU_URL
            ).rstrip("/"),
            mineru_backend=mineru_config.backend,
            mineru_server_url=mineru_config.server_url,
            host=values.get("PDF_TRANS_WEB_HOST", "127.0.0.1"),
            port=_positive_int(
                values, "PDF_TRANS_WEB_PORT", 8000, maximum=65535
            ),
        )
