# Contributing

Thanks for contributing to the Confidential ML Feature Store.

## Development workflow

1. Create a feature branch from `main`.
2. Keep changes scoped and reviewable.
3. Run formatting, linting, type checks, and tests before opening a pull request.
4. Update documentation when behavior, configuration, scripts, or endpoints change.

## Local setup

Install dependencies:

```bash
make install
```

Common commands:

```bash
make format
make lint
make test
make coverage
```

For local mock-enclave development:

```bash
docker compose up -d dynamodb-local
make run-dev-mock
```

## Code standards

* Preserve the current file structure and public interfaces unless there is a strong reason to change them.
* Use type hints throughout.
* Add or update docstrings for public functions, classes, and methods.
* Prefer explicit, meaningful error messages over generic failures.
* Keep request/response schemas and routes aligned with the existing FastAPI application.

## Tests

This project uses:

* `pytest`
* `moto`
* local mock components such as `MockEnclaveClient` and `MockNSM`

Before submitting a PR, make sure all tests pass:

```bash
make test
```

If you changed typing or formatting-sensitive code, also run:

```bash
make lint
```

## Documentation

When changing behavior in any of these areas, update the docs in the same PR:

* endpoint routes or schemas
* environment variables
* AWS setup steps
* scripts in `scripts/`
* enclave / mock-enclave behavior

If you add or update screenshots, place them in `docs/screenshots/` and update `docs/screenshots/README.md`.

## Pull requests

A good pull request should include:

* a concise summary of the change
* the reason for the change
* any migration or setup notes
* test evidence when behavior changed
