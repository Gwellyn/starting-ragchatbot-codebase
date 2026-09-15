"""
Tests for the FastAPI API layer (backend/app.py): /api/query, /api/courses,
DELETE /api/session/{id}, and /.

backend/app.py mounts `../frontend` as static files and constructs a real
RAGSystem at import time, neither of which exist/are safe in the test
environment. Rather than importing app.py, the `test_app` fixture
(backend/tests/conftest.py) rebuilds the same routes against a FakeRAGSystem,
so these tests exercise the actual request/response contract (Pydantic
validation, status codes, error propagation) without touching ChromaDB,
embeddings, or the Anthropic API.
"""

import pytest


@pytest.mark.api
class TestQueryEndpoint:
    def test_query_without_session_id_creates_one(self, client, fake_rag_system):
        response = client.post("/api/query", json={"query": "What is MCP?"})

        assert response.status_code == 200
        body = response.json()
        assert body["session_id"] == "test-session-id"
        assert body["answer"] == "This is a test answer."
        assert body["sources"] == [
            {"text": "Course A - Lesson 1", "link": "https://example.com/lesson1"}
        ]
        assert fake_rag_system.last_query_args == {
            "query": "What is MCP?",
            "session_id": "test-session-id",
        }

    def test_query_with_existing_session_id_is_reused(self, client, fake_rag_system):
        response = client.post(
            "/api/query",
            json={"query": "What is MCP?", "session_id": "existing-session"},
        )

        assert response.status_code == 200
        assert response.json()["session_id"] == "existing-session"
        assert fake_rag_system.last_query_args["session_id"] == "existing-session"
        fake_rag_system.session_manager.create_session.assert_not_called()

    def test_query_with_no_sources_returns_empty_list(self, client, fake_rag_system):
        fake_rag_system.query_response = ("General knowledge answer.", [])

        response = client.post("/api/query", json={"query": "What is 2+2?"})

        assert response.status_code == 200
        assert response.json()["sources"] == []

    def test_query_missing_query_field_returns_422(self, client):
        response = client.post("/api/query", json={"session_id": "abc"})

        assert response.status_code == 422

    def test_query_rag_system_error_returns_500(self, client, fake_rag_system):
        fake_rag_system.query_error = RuntimeError("vector store unavailable")

        response = client.post("/api/query", json={"query": "What is MCP?"})

        assert response.status_code == 500
        assert response.json()["detail"] == "vector store unavailable"

    def test_query_source_without_link_omits_it_as_null(self, client, fake_rag_system):
        fake_rag_system.query_response = (
            "Answer.",
            [{"text": "Course A - Lesson 2", "link": None}],
        )

        response = client.post("/api/query", json={"query": "question"})

        assert response.status_code == 200
        assert response.json()["sources"] == [
            {"text": "Course A - Lesson 2", "link": None}
        ]


@pytest.mark.api
class TestCoursesEndpoint:
    def test_get_course_stats_returns_analytics(self, client):
        response = client.get("/api/courses")

        assert response.status_code == 200
        assert response.json() == {
            "total_courses": 2,
            "course_titles": ["Course A", "Course B"],
        }

    def test_get_course_stats_with_no_courses(self, client, fake_rag_system):
        fake_rag_system.course_analytics = {"total_courses": 0, "course_titles": []}

        response = client.get("/api/courses")

        assert response.status_code == 200
        assert response.json() == {"total_courses": 0, "course_titles": []}

    def test_get_course_stats_error_returns_500(self, client, fake_rag_system):
        fake_rag_system.analytics_error = RuntimeError("chromadb unavailable")

        response = client.get("/api/courses")

        assert response.status_code == 500
        assert response.json()["detail"] == "chromadb unavailable"


@pytest.mark.api
class TestSessionEndpoint:
    def test_clear_session_returns_success(self, client, fake_rag_system):
        response = client.delete("/api/session/some-session-id")

        assert response.status_code == 200
        assert response.json() == {"success": True}
        fake_rag_system.session_manager.clear_session.assert_called_once_with(
            "some-session-id"
        )

    def test_clear_session_error_returns_500(self, client, fake_rag_system):
        fake_rag_system.session_manager.clear_session.side_effect = RuntimeError(
            "session store unavailable"
        )

        response = client.delete("/api/session/some-session-id")

        assert response.status_code == 500
        assert response.json()["detail"] == "session store unavailable"


@pytest.mark.api
class TestRootEndpoint:
    def test_root_returns_200(self, client):
        response = client.get("/")

        assert response.status_code == 200
