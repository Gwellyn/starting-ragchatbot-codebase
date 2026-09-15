"""
Tests for RAGSystem.query() (backend/rag_system.py) handling content-related
questions: does it wire the query, conversation history, tools, and
tool_manager together correctly, and round-trip sources from the search tool
back to the caller?

RAGSystem.__init__ constructs real DocumentProcessor/VectorStore/AIGenerator/
SessionManager instances, which would otherwise require a live ChromaDB
directory and a real Anthropic client. Every component except SessionManager
is monkeypatched at the class level before construction so these tests stay
fast and hermetic; SessionManager is cheap (in-memory) and left real to prove
history is actually persisted.
"""

from unittest.mock import MagicMock

import pytest
import rag_system
from rag_system import RAGSystem


class FakeConfig:
    CHUNK_SIZE = 800
    CHUNK_OVERLAP = 100
    CHROMA_PATH = "./unused_test_chroma"
    EMBEDDING_MODEL = "unused"
    MAX_RESULTS = 5
    MAX_HISTORY = 2
    ANTHROPIC_API_KEY = "fake-key"
    ANTHROPIC_MODEL = "claude-sonnet-5"


@pytest.fixture
def rag(monkeypatch):
    monkeypatch.setattr(rag_system, "DocumentProcessor", lambda *a, **k: MagicMock())
    monkeypatch.setattr(rag_system, "VectorStore", lambda *a, **k: MagicMock())
    fake_ai_generator = MagicMock()
    monkeypatch.setattr(rag_system, "AIGenerator", lambda *a, **k: fake_ai_generator)

    system = RAGSystem(FakeConfig())
    return system, fake_ai_generator


class TestQueryWiring:
    def test_query_wraps_question_and_forwards_tools(self, rag):
        system, ai_generator_mock = rag
        ai_generator_mock.generate_response.return_value = "the answer"

        system.query("What is MCP?")

        _, kwargs = ai_generator_mock.generate_response.call_args
        assert "What is MCP?" in kwargs["query"]
        assert kwargs["tool_manager"] is system.tool_manager
        tool_names = {t["name"] for t in kwargs["tools"]}
        assert tool_names == {"search_course_content", "get_course_outline"}

    def test_query_without_session_id_passes_no_history(self, rag):
        system, ai_generator_mock = rag
        ai_generator_mock.generate_response.return_value = "the answer"

        system.query("What is MCP?")

        _, kwargs = ai_generator_mock.generate_response.call_args
        assert kwargs["conversation_history"] is None

    def test_query_returns_answer_and_sources_from_tool_manager(self, rag):
        system, ai_generator_mock = rag
        ai_generator_mock.generate_response.return_value = "MCP is a protocol."
        system.tool_manager.get_last_sources = MagicMock(
            return_value=[{"text": "MCP Course - Lesson 1", "link": "http://x"}]
        )
        system.tool_manager.reset_sources = MagicMock()

        answer, sources = system.query("What is MCP?")

        assert answer == "MCP is a protocol."
        assert sources == [{"text": "MCP Course - Lesson 1", "link": "http://x"}]
        system.tool_manager.reset_sources.assert_called_once()

    def test_sources_reset_happens_after_reading_them_not_before(self, rag):
        """Reset must run after get_last_sources, or every response's sources
        would be silently dropped before the caller ever sees them."""
        system, ai_generator_mock = rag
        ai_generator_mock.generate_response.return_value = "answer"
        calls = []
        system.tool_manager.get_last_sources = MagicMock(
            side_effect=lambda: calls.append("get") or [{"text": "s", "link": None}]
        )
        system.tool_manager.reset_sources = MagicMock(
            side_effect=lambda: calls.append("reset")
        )

        answer, sources = system.query("anything")

        assert calls == ["get", "reset"]
        assert sources == [{"text": "s", "link": None}]


class TestSessionHistory:
    def test_creates_session_history_after_first_exchange(self, rag):
        system, ai_generator_mock = rag
        ai_generator_mock.generate_response.return_value = "first answer"
        session_id = system.session_manager.create_session()

        system.query("first question", session_id=session_id)

        history = system.session_manager.get_conversation_history(session_id)
        assert "first question" in history
        assert "first answer" in history

    def test_second_query_in_session_receives_prior_history(self, rag):
        system, ai_generator_mock = rag
        session_id = system.session_manager.create_session()
        ai_generator_mock.generate_response.return_value = "first answer"
        system.query("first question", session_id=session_id)

        ai_generator_mock.generate_response.return_value = "second answer"
        system.query("second question", session_id=session_id)

        _, kwargs = ai_generator_mock.generate_response.call_args
        assert "first question" in kwargs["conversation_history"]
        assert "first answer" in kwargs["conversation_history"]


class TestErrorPropagation:
    def test_exception_from_ai_generator_propagates_to_caller(self, rag):
        """
        RAGSystem.query() has no try/except of its own - if AIGenerator (or
        anything it calls, including CourseSearchTool.execute via the tool
        loop) raises, it propagates straight out of query(). app.py's
        /api/query endpoint catches this as a bare Exception and returns
        HTTP 500, which the frontend surfaces as the generic "Query failed."
        This test documents that contract so a regression here is caught
        directly instead of only being visible as a live "Query failed" bug.
        """
        system, ai_generator_mock = rag
        ai_generator_mock.generate_response.side_effect = RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            system.query("What is MCP?")
