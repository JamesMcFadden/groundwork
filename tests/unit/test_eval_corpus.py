import pytest

from eval.corpus import CorpusError, parse_manifest

FIRST = "a" * 64
SECOND = "b" * 64


def test_a_shasum_manifest_maps_each_filename_to_its_digest_in_order() -> None:
    """Text and binary mode lines both parse, and blank lines are ignored."""
    text = f"{FIRST}  report.pdf\n\n{SECOND} *other report.pdf\n"

    assert list(parse_manifest(text).items()) == [
        ("report.pdf", FIRST),
        ("other report.pdf", SECOND),
    ]


def test_a_malformed_line_is_rejected_by_number() -> None:
    with pytest.raises(CorpusError, match="line 2"):
        parse_manifest(f"{FIRST}  report.pdf\n{'a' * 63}  short.pdf\n")


def test_an_uppercase_digest_is_rejected() -> None:
    """shasum writes lowercase; anything else was edited by hand and may be mistyped."""
    with pytest.raises(CorpusError, match="line 1"):
        parse_manifest(f"{'A' * 64}  report.pdf\n")


def test_a_filename_listed_twice_is_rejected() -> None:
    with pytest.raises(CorpusError, match="twice"):
        parse_manifest(f"{FIRST}  report.pdf\n{SECOND}  report.pdf\n")


@pytest.mark.parametrize("name", ["../report.pdf", "nested/report.pdf", "nested\\report.pdf"])
def test_a_path_outside_the_corpus_directory_is_rejected(name: str) -> None:
    with pytest.raises(CorpusError, match="path"):
        parse_manifest(f"{FIRST}  {name}\n")


def test_an_empty_manifest_is_rejected() -> None:
    with pytest.raises(CorpusError, match="no documents"):
        parse_manifest("\n")
