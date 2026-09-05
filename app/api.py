from typing import Any

from fastapi import APIRouter, Request, Response, status

from app.db.session import database_ok

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
def ready(request: Request, response: Response) -> dict[str, Any]:
    """Readiness: should this pod receive traffic?

    Unlike liveness, this fails when a dependency is unreachable so that traffic stops
    routing here until it recovers.
    """
    checks = {"database": "ok" if database_ok(request.app.state.engine) else "unavailable"}
    ready = all(value == "ok" for value in checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ready else "not ready", "checks": checks}
