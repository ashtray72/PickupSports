"""
PickupSports FastAPI app.

Run from the repository root (database running and seeded, see AGENTS.md):

    python -m uvicorn server.app.main:app --reload --host 0.0.0.0 --port 8000

Interactive docs: http://localhost:8000/docs
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from server.app.api.v1 import games
from server.app.crud.errors import (
    GameFullError,
    GameNotJoinableError,
    NotFoundError,
    NotPermittedError,
    PickupDBError,
)

app = FastAPI(title="PickupSports API", version="0.1.0")
app.include_router(games.router, prefix="/api/v1")


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


# Domain errors from the CRUD layer -> HTTP status codes (see crud/errors.py).
ERROR_STATUS = [
    (NotFoundError, 404),
    (GameFullError, 409),
    (GameNotJoinableError, 409),
    (NotPermittedError, 403),
]


@app.exception_handler(PickupDBError)
async def handle_domain_error(request: Request, exc: PickupDBError) -> JSONResponse:
    status_code = 400
    for error_type, code in ERROR_STATUS:
        if isinstance(exc, error_type):
            status_code = code
            break
    return JSONResponse(status_code=status_code, content={"detail": str(exc)})
