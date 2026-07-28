# The `frontend` container: the Streamlit dashboard, a thin client that talks
# only to the backend API (never the database or an LLM directly).
FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ src/

RUN pip install --no-cache-dir ".[frontend]"

WORKDIR /app/src/echo/frontend
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.address", "0.0.0.0", "--server.port", "8501"]
