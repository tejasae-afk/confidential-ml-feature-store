PYTHON ?= python3.11

install:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -r requirements.txt -r enclave/requirements.txt

test:
	pytest

lint:
	ruff check .
	mypy feature_store enclave tests scripts/train_model.py

format:
	ruff format .

build-enclave:
	./scripts/build_enclave.sh

run-enclave:
	./scripts/run_enclave.sh

run-local:
	docker compose up --build

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov build dist .venv
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete
