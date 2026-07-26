import importlib.util


def test_pdf_trans_is_the_only_supported_package_name():
    assert importlib.util.find_spec("pdf_trans") is not None
    assert importlib.util.find_spec("mineru_cleaner") is None
