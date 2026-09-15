# Backend testing infrastructure

Note: despite the filename, this change is entirely backend (pytest/FastAPI test tooling) — no frontend files were touched. Logged here per instruction.

## What changed

- **`pyproject.toml`**: added `[tool.pytest.ini_options]` (`testpaths = ["backend/tests"]`, `pythonpath = ["backend"]`, `addopts = "-ra --strict-markers"`, `api`/`integration` markers) so `uv run pytest` works from either the repo root or `backend/` without extra flags. Added `httpx` as a dev dependency via `uv add --dev httpx` (required by FastAPI's `TestClient`).
- **`backend/tests/conftest.py`**: added a `FakeRAGSystem` fixture (`fake_rag_system`) and a `test_app` fixture that rebuilds the same routes as `backend/app.py` (`/api/query`, `DELETE /api/session/{id}`, `/api/courses`, `/`) against the fake, plus a `client` fixture wrapping it in `TestClient`.
- **`backend/tests/test_api_endpoints.py`** (new): 15 tests covering `/api/query` (session creation vs. reuse, empty sources, 422 on missing `query`, 500 on RAG errors, null `link` handling), `/api/courses` (happy path, zero courses, 500 on errors), `DELETE /api/session/{id}` (success and error), and `/`.

## Why a separate test app instead of importing `backend/app.py`

`app.py` mounts `../frontend` as static files and constructs a real `RAGSystem` (ChromaDB + embeddings + Anthropic client) at **module import time**. Importing it in a test process would fail (missing `frontend/` dir) or require a live API key/vector store. `conftest.py`'s `test_app` fixture defines the same three endpoints inline against a `FakeRAGSystem`, so tests exercise the real request/response contract (Pydantic validation, status codes, error propagation) without those dependencies. If `app.py`'s route logic changes, `test_app` needs a matching update — there's no shared source of truth enforcing they stay in sync.

The `/` route in the test app is a minimal JSON stub (`{"message": "..."}`) rather than the real static-file/index.html mount, since there's no `frontend/` directory to serve in the test environment.

## Verification

`uv run pytest` — 55 passed, 4 skipped (pre-existing live-integration tests that skip without an ingested ChromaDB), 0 failed. Confirmed working from both the repo root and `backend/`.
