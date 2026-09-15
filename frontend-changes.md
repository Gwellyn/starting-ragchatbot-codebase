# Code Quality Tooling

Added `black` as the code formatter for the codebase and made formatting consistent across all existing Python source.

## Changes

- **Dependency**: Added `black` to the `dev` dependency group in `pyproject.toml` (installed via `uv add --dev black`).
- **Configuration**: Added a `[tool.black]` section to `pyproject.toml` (line length 88, target `py313`, excludes `.venv/` and `docs/`).
- **Formatting pass**: Ran `black` across `backend/` and `main.py`, reformatting 13 files for consistent quoting, spacing, and blank-line conventions. No behavior changes — full backend test suite (`uv run pytest`, run from `backend/`) still passes (43 passed, 4 skipped).
- **Dev scripts** (new `scripts/` directory):
  - `scripts/format.sh` — runs `black` and rewrites files in place.
  - `scripts/check.sh` — runs `black --check --diff`, exits non-zero if anything is unformatted (CI-friendly, makes no changes).

## Usage

```bash
./scripts/format.sh   # auto-format backend/ and main.py
./scripts/check.sh    # verify formatting only, no changes
```

## Note on scope

The task requested this be scoped to "front-end features," but `black` is a Python formatter and this repository's frontend (`frontend/`) is plain JS/HTML/CSS with no Python or build tooling. Per user clarification, this was implemented for the Python backend instead, since that's the only place `black` applies. No frontend files were changed.
