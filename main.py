"""Compatibility entrypoint.

Render / uvicorn commands that reference `main:app` will keep working.
The real application lives in `app/main.py`.
"""

from app.main import app  # noqa: F401

