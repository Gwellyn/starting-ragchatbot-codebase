import sys
from pathlib import Path

# Tests import backend modules with flat names (e.g. `from vector_store import
# VectorStore`), matching how the app itself imports them - so the backend/
# directory needs to be on sys.path, same as when running `cd backend && uv
# run uvicorn app:app`.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
