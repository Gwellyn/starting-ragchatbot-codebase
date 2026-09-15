"""
End-to-end smoke tests against the REAL ChromaDB (backend/chroma_db) and the
REAL Anthropic API - no mocks. These are the tests most likely to reproduce
"query returns failed for any content-related question" if the cause is
environmental (missing API key, empty/corrupt vector store, an invalid
model name, a real network/SDK incompatibility) rather than a pure logic bug
in search_tools.py / ai_generator.py / rag_system.py.

Skipped automatically when there's no API key or no ingested course data,
so the rest of the suite still runs in CI/offline.
"""

import os

import pytest
from config import config
from rag_system import RAGSystem

pytestmark = pytest.mark.skipif(
    not config.ANTHROPIC_API_KEY,
    reason="ANTHROPIC_API_KEY not set - skipping live API tests",
)


@pytest.fixture(scope="module")
def live_rag():
    system = RAGSystem(config)
    if system.vector_store.get_course_count() == 0:
        pytest.skip("No courses ingested in chroma_db - run the app once to load docs/")
    return system


class TestLiveContentQuery:
    def test_content_question_triggers_search_and_returns_grounded_answer(self, live_rag):
        answer, sources = live_rag.query("What is MCP and how does it work?")

        assert answer, "Expected a non-empty answer for a content question"
        assert sources, "Expected search_course_content to populate sources for a content question"

    def test_outline_question_returns_lesson_list(self, live_rag):
        courses = live_rag.vector_store.get_existing_course_titles()
        assert courses, "No course titles found in the vector store"

        answer, _ = live_rag.query(f"What lessons are in the course '{courses[0]}'?")

        assert answer

    def test_nonexistent_course_name_does_not_crash(self, live_rag):
        """
        _resolve_course_name has no similarity threshold: it always returns
        the nearest catalog match, even for a name with no real match. This
        checks that at minimum querying a bogus course name fails softly
        (an answer is still returned) rather than throwing - the crash case
        is what would surface to a user as "Query failed."
        """
        answer, _ = live_rag.query(
            "Tell me about lesson 1 of the course 'Underwater Basket Weaving 404'"
        )
        assert answer

    def test_two_turn_conversation_in_same_session(self, live_rag):
        session_id = live_rag.session_manager.create_session()
        first_answer, _ = live_rag.query("What is MCP?", session_id=session_id)
        assert first_answer

        second_answer, _ = live_rag.query(
            "What lesson number introduces it?", session_id=session_id
        )
        assert second_answer
