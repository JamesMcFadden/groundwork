"""Authenticating requests with the static API key.

One key, from `API_KEY`, admits every request and maps it to the seeded user. A table of
hashed keys, which a second tenant would need, is parked in docs/roadmap.md.
"""

import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from app.config import Settings

# auto_error off, so a missing key gets the same 401 as a wrong one rather than FastAPI's
# own response.
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


class ApiKeyConfigError(RuntimeError):
    """The API has no key to check requests against."""


def configured_api_key(settings: Settings) -> bytes:
    """Return the key requests must present, failing if none is set.

    Called when the API starts, so a deployment without a key never serves a request. An
    empty key is refused too: it would admit a request sending an empty header.
    """
    key = settings.api_key
    if key is None or not key.get_secret_value():
        raise ApiKeyConfigError("the API needs API_KEY, the key clients send in X-API-Key")
    return key.get_secret_value().encode()


def require_api_key(
    request: Request, presented: Annotated[str | None, Depends(api_key_header)]
) -> None:
    """Refuse a request that does not present the configured key.

    A missing and a wrong key get the same response, which says nothing about which it
    was. `compare_digest` takes as long wherever the two keys first differ.
    """
    expected: bytes = request.app.state.api_key
    if presented is None or not hmac.compare_digest(presented.encode(), expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="missing or invalid API key"
        )
