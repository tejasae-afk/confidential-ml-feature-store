PYTHON ?= python3.11
PIP ?= $(PYTHON) -m pip
APP_HOST ?= 0.0.0.0
APP_PORT ?= 8000
AWS_REGION ?= us-east-1
DYNAMODB_TABLE_NAME ?= confidential-ml-feature-store
KMS_KEY_ID ?= local-placeholder-kms-key
TENANT_ID ?= tenant-1
MODEL_NAME ?= test-model
MODEL_OUTPUT_DIR ?= artifacts

.PHONY: help install test lint format typecheck coverage build-enclave run-enclave \
	run-local run-app run-dev-mock docker-up docker-up-dynamodb docker-down \
	setup-dynamodb setup-kms train-model ci clean

help:
	@echo "Available targets:"
	@echo "  install            Install runtime, enclave, and dev dependencies"
	@echo "  test               Run pytest"
	@echo "  lint               Run ruff and mypy"
	@echo "  format             Run ruff formatter"
	@echo "  typecheck          Run mypy"
	@echo "  coverage           Run pytest under coverage and write coverage.xml"
	@echo "  docker-up          Start all docker-compose services"
	@echo "  docker-up-dynamodb Start only DynamoDB Local"
	@echo "  docker-down        Stop docker-compose services"
	@echo "  run-local          Run all docker-compose services in the foreground"
	@echo "  run-app            Run uvicorn against the host environment"
	@echo "  run-dev-mock       Run uvicorn with USE_MOCK_ENCLAVE=true and local DynamoDB"
	@echo "  setup-dynamodb     Run scripts/setup_dynamodb.sh"
	@echo "  setup-kms          Run scripts/setup_kms.sh"
	@echo "  train-model        Run scripts/train_model.py with default tenant/model values"
	@echo "  build-enclave      Build the EIF using scripts/build_enclave.sh"
	@echo "  run-enclave        Run the EIF using scripts/run_enclave.sh"
	@echo "  ci                 Run lint, tests, and coverage"
	@echo "  clean              Remove local caches and build artifacts"

install:
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt -r enclave/requirements.txt cryptography coverage

test:
	pytest -v

lint:
	ruff check .
	mypy feature_store enclave tests scripts/train_model.py

format:
	ruff format .

typecheck:
	mypy feature_store enclave tests scripts/train_model.py

coverage:
	$(PYTHON) -m coverage erase
	$(PYTHON) -m coverage run -m pytest -v
	$(PYTHON) -m coverage xml -o coverage.xml
	$(PYTHON) -m coverage report -m

docker-up:
	docker compose up -d

docker-up-dynamodb:
	docker compose up -d dynamodb-local

docker-down:
	docker compose down

run-local:
	docker compose up --build

run-app:
	uvicorn feature_store.main:app --host $(APP_HOST) --port $(APP_PORT) --reload

run-dev-mock:
	DYNAMODB_ENDPOINT=http://localhost:8001 USE_MOCK_ENCLAVE=true \
	uvicorn feature_store.main:app --host $(APP_HOST) --port $(APP_PORT) --reload

setup-dynamodb:
	./scripts/setup_dynamodb.sh

setup-kms:
	./scripts/setup_kms.sh

train-model:
	$(PYTHON) scripts/train_model.py \
		--tenant-id "$(TENANT_ID)" \
		--model-name "$(MODEL_NAME)" \
		--output-dir "$(MODEL_OUTPUT_DIR)" \
		--kms-key-id "$(KMS_KEY_ID)" \
		--aws-region "$(AWS_REGION)"

build-enclave:
	./scripts/build_enclave.sh

run-enclave:
	./scripts/run_enclave.sh

ci: lint test coverage

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov build dist .venv
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete
