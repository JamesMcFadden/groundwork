from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
def live() -> dict[str, str]:
    """Liveness: is this process responsive?

    Deliberately checks nothing external. If this probe consulted the database, a brief
    database outage would cause Kubernetes to restart every API pod at once, turning a
    recoverable dependency failure into a total one.
    """
    return {"status": "alive"}


@router.get("/ready")
def ready() -> dict[str, Any]:
    """Readiness: should this pod receive traffic?

    Unlike liveness, this should fail when a dependency is unreachable so traffic stops
    routing here. `checks` is empty until the database layer exists.
    """
    return {"status": "ready", "checks": {}}
