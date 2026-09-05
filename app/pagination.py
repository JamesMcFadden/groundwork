import base64
import binascii
import json
import uuid
from datetime import datetime


def encode_cursor(created_at: datetime, item_id: uuid.UUID) -> str:
    """Encode a sort position as an opaque token.

    Clients treat this as untyped and hand it back verbatim, which keeps the sort key
    out of the public API contract.
    """
    payload = json.dumps({"c": created_at.isoformat(), "i": str(item_id)})
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Decode a cursor, raising ValueError if it is not one we issued."""
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return datetime.fromisoformat(payload["c"]), uuid.UUID(payload["i"])
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError("malformed cursor") from exc
