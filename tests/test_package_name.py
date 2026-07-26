import importlib.util


def test_pdf_trans_is_the_only_supported_package_name():
    assert importlib.util.find_spec("pdf_trans") is not None
    old_package_name = "mineru_" + "cleaner"
    assert importlib.util.find_spec(old_package_name) is None
