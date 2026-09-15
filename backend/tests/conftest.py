import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

# Tests import backend modules with flat names (e.g. `from vector_store import
# VectorStore`), matching how the app itself imports them - so the backend/
# directory needs to be on sys.path, same as when running `cd backend && uv
# run uvicorn app:app`.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


class FakeRAGSystem:
    """
    Stands in for RAGSystem in API-layer tests so the FastAPI endpoints can be
    exercised without a real ChromaDB store, embeddings model, or Anthropic
    client. Return values are plain attributes so individual tests can
    override them to drive success/error paths.
    """

    def __init__(self):
        self.query_response: Tuple[str, List[Dict[str, Any]]] = (
            "This is a test answer.",
            [{"text": "Course A - Lesson 1", "link": "https://example.com/lesson1"}],
        )
        self.query_error: Optional[Exception] = None
        self.last_query_args: Optional[Dict[str, Any]] = None

        self.course_analytics: Dict[str, Any] = {
            "total_courses": 2,
            "course_titles": ["Course A", "Course B"],
        }
        self.analytics_error: Optional[Exception] = None

        self.session_manager = MagicMock()
        self.session_manager.create_session.return_value = "test-session-id"

    def query(self, query: str, session_id: Optional[str] = None):
        self.last_query_args = {"query": query, "session_id": session_id}
        if self.query_error:
            raise self.query_error
        return self.query_response

    def get_course_analytics(self):
        if self.analytics_error:
            raise self.analytics_error
        return self.course_analytics


@pytest.fixture
def fake_rag_system():
    return FakeRAGSystem()


@pytest.fixture
def test_app(fake_rag_system):
    """
    A FastAPI app that mirrors backend/app.py's API endpoints, built inline
    instead of importing app.py directly. app.py mounts ../frontend as static
    files at import time, which doesn't exist in the test environment and
    would raise on import; it also constructs a real RAGSystem at module
    scope. Redefining the routes here against a fake RAGSystem keeps these
    tests hermetic while still exercising the same request/response contracts
    (Pydantic models, status codes, error handling).
    """
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel

    app = FastAPI(title="Course Materials RAG System (test)")

    class QueryRequest(BaseModel):
        query: str
        session_id: Optional[str] = None

    class Source(BaseModel):
        text: str
        link: Optional[str] = None

    class QueryResponse(BaseModel):
        answer: str
        sources: List[Source]
        session_id: str

    class CourseStats(BaseModel):
        total_courses: int
        course_titles: List[str]

    @app.post("/api/query", response_model=QueryResponse)
    async def query_documents(request: QueryRequest):
        try:
            session_id = request.session_id
            if not session_id:
                session_id = fake_rag_system.session_manager.create_session()

            answer, sources = fake_rag_system.query(request.query, session_id)

            return QueryResponse(answer=answer, sources=sources, session_id=session_id)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/session/{session_id}")
    async def clear_session(session_id: str):
        try:
            fake_rag_system.session_manager.clear_session(session_id)
            return {"success": True}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/courses", response_model=CourseStats)
    async def get_course_stats():
        try:
            analytics = fake_rag_system.get_course_analytics()
            return CourseStats(
                total_courses=analytics["total_courses"],
                course_titles=analytics["course_titles"],
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/")
    async def root():
        return {"message": "Course Materials RAG System"}

    return app


@pytest.fixture
def client(test_app):
    from fastapi.testclient import TestClient

    return TestClient(test_app)
