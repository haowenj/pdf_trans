from importlib.resources import files

from pdf_trans.web import __main__ as web_main


def test_main_runs_single_uvicorn_process(monkeypatch) -> None:
    received: dict[str, object] = {}
    monkeypatch.setattr(
        web_main,
        "create_app",
        lambda settings: "application",
    )
    monkeypatch.setattr(
        web_main.uvicorn,
        "run",
        lambda app, **kwargs: received.update(app=app, **kwargs),
    )

    assert (
        web_main.main(
            environ={
                "PDF_TRANS_WEB_HOST": "0.0.0.0",
                "PDF_TRANS_WEB_PORT": "8123",
            }
        )
        == 0
    )
    assert received == {
        "app": "application",
        "host": "0.0.0.0",
        "port": 8123,
        "workers": 1,
    }


def test_web_package_contains_runtime_resources() -> None:
    package = files("pdf_trans.web")
    assert package.joinpath("templates/dashboard.html").is_file()
    assert package.joinpath("templates/reader.html").is_file()
    assert package.joinpath("static/dashboard.js").is_file()
    assert package.joinpath("static/vendor/katex/katex.min.js").is_file()
    assert package.joinpath(
        "migrations/versions/0001_create_web_tasks.py"
    ).is_file()
