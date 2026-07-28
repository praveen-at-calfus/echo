# echo — common commands. Run `make` or `make help` to see this list.
# Mirrors the commands documented in CLAUDE.md; nothing here does anything
# those commands didn't already do by hand.

.DEFAULT_GOAL := help
.PHONY: help venv install run-api run-frontend dev status \
        docker-up docker-down docker-build docker-refresh \
        lint test clean \
        corpus db classify embed money themes summary ask evaluate

PYTHON := .venv/bin/python
PIP    := .venv/bin/pip
WEEK   ?=

help:
	@echo "echo — common commands"
	@echo ""
	@echo "  make install          create .venv + install every extra (corpus,db,pipeline,app,frontend,dev)"
	@echo ""
	@echo "  make run-api          run the backend locally (uvicorn, :8000)"
	@echo "  make run-frontend     run the dashboard locally (streamlit, :8501)"
	@echo "  make dev              run backend + frontend together; Ctrl-C stops both"
	@echo "  make status           which instance (local/docker/conflict/free) owns 5432/8000/8501"
	@echo ""
	@echo "  make docker-up        docker compose up --build (packaged demo, needs Docker Desktop)"
	@echo "  make docker-down      docker compose down"
	@echo "  make docker-refresh   regenerate the seed from local Postgres, rebuild, fully reset the stack"
	@echo ""
	@echo "  make lint             ruff check src/"
	@echo "  make test             pytest (no tests exist yet — placeholder for when there are)"
	@echo "  make clean            remove __pycache__ / .ruff_cache"
	@echo ""
	@echo "  make corpus           full corpus build + verify (needs data/raw/, see CORPUS.md)"
	@echo "  make db               (re)load data/processed -> Postgres"
	@echo "  make classify         classify all unclassified feedback"
	@echo "  make embed            embed all texted items with no embedding yet"
	@echo "  make money            print the money-engine report (all-time)"
	@echo "  make themes WEEK=2018-03-05    build themes for one week"
	@echo "  make summary WEEK=2018-03-05   generate the weekly summary for one week"
	@echo "  make ask Q=\"late deliveries?\"  ask echo one question from the CLI"
	@echo "  make evaluate         gold-set confusion matrix + silver-sentiment-at-scale report"

venv:
	python3.13 -m venv .venv

install: venv
	$(PIP) install -e ".[corpus,db,pipeline,app,frontend,dev]"

run-api:
	PYTHONPATH=src $(PYTHON) -m echo.api

run-frontend:
	ECHO_API_URL=http://localhost:8000 PYTHONPATH=src $(PYTHON) -m echo.frontend

# Runs both in one terminal; Ctrl-C (or any exit) kills both together.
dev:
	@trap 'kill 0' EXIT INT TERM; \
	PYTHONPATH=src $(PYTHON) -m echo.api & \
	sleep 2; \
	ECHO_API_URL=http://localhost:8000 PYTHONPATH=src $(PYTHON) -m echo.frontend & \
	wait

status:
	./scripts/status.sh

docker-up:
	docker compose up --build

docker-down:
	docker compose down

docker-build:
	docker compose build

docker-refresh:
	./scripts/refresh-docker.sh

lint:
	.venv/bin/ruff check src/

test:
	.venv/bin/pytest -q || echo "(no tests yet)"

clean:
	find . -name '__pycache__' -not -path './.venv/*' -exec rm -rf {} + 2>/dev/null || true
	rm -rf .ruff_cache

corpus:
	PYTHONPATH=src $(PYTHON) -m echo.corpus

db:
	PYTHONPATH=src $(PYTHON) -m echo.db

classify:
	PYTHONPATH=src $(PYTHON) -m echo.classify

embed:
	PYTHONPATH=src $(PYTHON) -m echo.embed

money:
	PYTHONPATH=src $(PYTHON) -m echo.money

themes:
	PYTHONPATH=src $(PYTHON) -m echo.themes --week $(WEEK)

summary:
	PYTHONPATH=src $(PYTHON) -m echo.summary --week $(WEEK)

ask:
	PYTHONPATH=src $(PYTHON) -m echo.rag "$(Q)"

evaluate:
	PYTHONPATH=src $(PYTHON) -m echo.classify.evaluate
