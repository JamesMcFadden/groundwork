import uuid
from datetime import UTC, datetime

import pytest

from app.pagination import decode_cursor, encode_cursor


def test_cursor_round_trips() -> None:
    created_at = datetime(2026, 9, 5, 10, 0, 1, tzinfo=UTC)
    item_id = uuid.uuid4()

    decoded_at, decoded_id = decode_cursor(encode_cursor(created_at, item_id))

    assert decoded_at == created_at
    assert decoded_id == item_id


@pytest.mark.parametrize("cursor", ["not-base64!", "", "eyJib2d1cyI6IHRydWV9"])
def test_malformed_cursors_are_rejected(cursor: str) -> None:
    with pytest.raises(ValueError):
        decode_cursor(cursor)
