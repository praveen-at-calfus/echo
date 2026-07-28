"""The Streamlit frontend — a thin client that talks only to the echo API.

Plain English: this is the dashboard a CX/product leader actually opens. It
never touches the database or an LLM directly — every number and answer comes
from calling the FastAPI backend's JSON endpoints (see :mod:`echo.api`).

Run: ``python -m echo.frontend`` (or ``streamlit run src/echo/frontend/app.py``).
The API base URL is read from the ``ECHO_API_URL`` env var (default
``http://localhost:8000``).
"""

from __future__ import annotations
