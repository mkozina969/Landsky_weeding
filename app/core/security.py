import base64

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import ADMIN_PASSWORD, ADMIN_USER

security = HTTPBasic()


def require_admin(credentials: HTTPBasicCredentials = Depends(security)):
    if credentials.username != ADMIN_USER or credentials.password != ADMIN_PASSWORD:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )


def _check_basic_auth(request: Request) -> bool:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8")
        username, password = decoded.split(":", 1)
    except Exception:
        return False
    return username == ADMIN_USER and password == ADMIN_PASSWORD


def require_admin_request(request: Request) -> None:
    """Use this when a dependency isn't convenient (e.g., HTML admin page)."""
    if not _check_basic_auth(request):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": 'Basic realm="Landsky Admin"'},
        )
