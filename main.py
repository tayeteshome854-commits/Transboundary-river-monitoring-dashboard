"""Compatibility entry point.

The application now lives in the ``app`` package (see ``app/main.py``), but the
documented launch command stays the same:

    uvicorn main:app --host 0.0.0.0 --port 8000

Running this file directly (``python main.py``) starts the same server with
reload enabled for local development.
"""

from __future__ import annotations

import uvicorn

from app.config import settings
from app.main import app  # noqa: F401  (re-exported for `uvicorn main:app`)

__all__ = ["app"]


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=not settings.is_production,
    )
