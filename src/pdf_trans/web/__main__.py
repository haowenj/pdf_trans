from __future__ import annotations

import os
from collections.abc import Mapping, Sequence

import uvicorn

from pdf_trans.web.app import create_app
from pdf_trans.web.config import WebSettings


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    del argv
    settings = WebSettings.from_env(
        environ=os.environ if environ is None else environ
    )
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        workers=1,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
