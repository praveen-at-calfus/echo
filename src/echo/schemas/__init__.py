"""Shared data shapes for echo.

Plain English: this defines exactly what one piece of feedback looks like, so
every part of the system agrees on it. Technically: Pydantic models (see
``envelope.py``) reused by the corpus builder, the DB loader, and later stages.
"""
