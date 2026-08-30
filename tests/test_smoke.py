def test_package_imports() -> None:
    import app

    assert app.__version__
