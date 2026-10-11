# Shortcuts. Run `make help`. (Windows without make: use run_local.bat and the commands in README.md.)
PY := .venv/bin/python

.PHONY: help install env check-models check-models-live api ui mock-ui test eval eval-judge docker clean

help:              ## Show this help
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  make %-18s %s\n", $$1, $$2}'

install:           ## Create .venv and install all dependencies
	python3 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements-dev.txt

env:               ## Create .env from .env.example (if missing)
	@test -f .env || (cp .env.example .env && echo ".env created: add OPENROUTER_API_KEY")

check-models:      ## Validate the configured OpenRouter models (free)
	$(PY) scripts/check_models.py

check-models-live: ## Validate models with one tiny live request each
	$(PY) scripts/check_models.py --live

api:               ## Start FastAPI on http://localhost:8000 (docs at /docs)
	.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload $(addprefix --reload-dir ,api agents models orchestration prompts tools)

ui:                ## Start Streamlit on http://localhost:8501 (start the API first)
	.venv/bin/streamlit run app/streamlit_app.py

mock-ui:           ## Serve the clickable UI prototype (sample data only) on http://localhost:8600
	python3 -m http.server 8600 --bind 127.0.0.1 --directory frontend

test:              ## Run unit tests (no API key or network needed)
	$(PY) -m pytest

eval:              ## Run the eval suite on the golden dataset (real LLM calls, ~$0.10)
	$(PY) scripts/run_evals.py

eval-judge:        ## Eval suite plus LLM-as-judge scoring of remediation quality
	$(PY) scripts/run_evals.py --judge

docker:            ## Build and run API + UI with Docker Compose
	docker compose up --build

clean:             ## Remove caches (keeps the database, index and uploads)
	rm -rf .pytest_cache .ruff_cache
	find . -name __pycache__ -not -path './.venv/*' -exec rm -rf {} +
