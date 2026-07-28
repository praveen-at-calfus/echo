# The `backend` container: FastAPI + the whole pipeline (classify, embed, money,
# themes, summary, rag). Entrypoint is uvicorn-only — the TRUNCATE-ing corpus
# loader (`python -m echo.db`) must never run against a seeded database.
FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ src/

RUN pip install --no-cache-dir ".[corpus,db,pipeline,app]"

EXPOSE 8000
CMD ["uvicorn", "echo.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
