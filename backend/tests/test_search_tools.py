"""
Tests for CourseSearchTool.execute() (backend/search_tools.py).

The vector store is replaced with a lightweight fake so these tests exercise
only CourseSearchTool's own logic: result formatting, error propagation,
empty-result messaging, and source/link tracking for the UI.
"""

from unittest.mock import MagicMock

import pytest
from search_tools import CourseSearchTool
from vector_store import SearchResults


class FakeVectorStore:
    """Stands in for VectorStore so tests aren't coupled to ChromaDB/embeddings."""

    def __init__(self, search_result: SearchResults,
                 lesson_links=None, course_links=None):
        self.search_result = search_result
        self.lesson_links = lesson_links or {}
        self.course_links = course_links or {}
        self.last_search_kwargs = None

    def search(self, query, course_name=None, lesson_number=None, limit=None):
        self.last_search_kwargs = {
            "query": query,
            "course_name": course_name,
            "lesson_number": lesson_number,
            "limit": limit,
        }
        return self.search_result

    def get_lesson_link(self, course_title, lesson_number):
        return self.lesson_links.get((course_title, lesson_number))

    def get_course_link(self, course_title):
        return self.course_links.get(course_title)


def make_results(rows):
    """rows: list of (document_text, course_title, lesson_number)"""
    documents = [r[0] for r in rows]
    metadata = [{"course_title": r[1], "lesson_number": r[2]} for r in rows]
    distances = [0.1 * i for i in range(len(rows))]
    return SearchResults(documents=documents, metadata=metadata, distances=distances)


class TestExecuteHappyPath:
    def test_formats_single_result_with_course_and_lesson_header(self):
        store = FakeVectorStore(make_results([
            ("MCP uses a client-server architecture.", "MCP Course", 1),
        ]))
        tool = CourseSearchTool(store)

        result = tool.execute(query="What is MCP?")

        assert "[MCP Course - Lesson 1]" in result
        assert "MCP uses a client-server architecture." in result

    def test_formats_multiple_results_separated_by_blank_line(self):
        store = FakeVectorStore(make_results([
            ("First chunk.", "Course A", 1),
            ("Second chunk.", "Course A", 2),
        ]))
        tool = CourseSearchTool(store)

        result = tool.execute(query="anything")

        assert "[Course A - Lesson 1]\nFirst chunk." in result
        assert "[Course A - Lesson 2]\nSecond chunk." in result
        assert result.count("\n\n") >= 1

    def test_header_omits_lesson_when_lesson_number_is_none(self):
        store = FakeVectorStore(make_results([("Some text.", "Course A", None)]))
        tool = CourseSearchTool(store)

        result = tool.execute(query="anything")

        assert result.startswith("[Course A]")
        assert "Lesson" not in result.split("\n", 1)[0]

    def test_passes_query_course_name_and_lesson_number_to_store(self):
        store = FakeVectorStore(make_results([("text", "Course A", 1)]))
        tool = CourseSearchTool(store)

        tool.execute(query="what is X", course_name="Course A", lesson_number=1)

        assert store.last_search_kwargs == {
            "query": "what is X",
            "course_name": "Course A",
            "lesson_number": 1,
            "limit": None,
        }


class TestExecuteErrorHandling:
    def test_returns_store_error_message_verbatim(self):
        store = FakeVectorStore(SearchResults.empty("No course found matching 'Nonexistent'"))
        tool = CourseSearchTool(store)

        result = tool.execute(query="anything", course_name="Nonexistent")

        assert result == "No course found matching 'Nonexistent'"

    def test_search_error_from_chroma_is_surfaced(self):
        store = FakeVectorStore(SearchResults.empty("Search error: connection refused"))
        tool = CourseSearchTool(store)

        result = tool.execute(query="anything")

        assert result == "Search error: connection refused"


class TestExecuteEmptyResults:
    def test_empty_results_no_filters(self):
        store = FakeVectorStore(SearchResults(documents=[], metadata=[], distances=[]))
        tool = CourseSearchTool(store)

        result = tool.execute(query="anything")

        assert result == "No relevant content found."

    def test_empty_results_mentions_course_filter(self):
        store = FakeVectorStore(SearchResults(documents=[], metadata=[], distances=[]))
        tool = CourseSearchTool(store)

        result = tool.execute(query="anything", course_name="Course A")

        assert "in course 'Course A'" in result

    def test_empty_results_mentions_lesson_filter(self):
        store = FakeVectorStore(SearchResults(documents=[], metadata=[], distances=[]))
        tool = CourseSearchTool(store)

        result = tool.execute(query="anything", lesson_number=3)

        assert "in lesson 3" in result

    def test_empty_results_lesson_zero_filter_is_reported(self):
        """
        Lesson 0 is a real, valid lesson (documents use `Lesson 0: <title>`
        for intros). CourseSearchTool.execute builds this message with
        `if lesson_number:`, which is False for 0, so the filter is silently
        dropped from the message - a genuine (if cosmetic) bug.
        """
        store = FakeVectorStore(SearchResults(documents=[], metadata=[], distances=[]))
        tool = CourseSearchTool(store)

        result = tool.execute(query="anything", lesson_number=0)

        assert "in lesson 0" in result, (
            "execute() uses a truthy check (`if lesson_number:`) instead of "
            "`is not None`, so lesson_number=0 is dropped from the message. "
            f"Got: {result!r}"
        )


class TestSourceTracking:
    def test_sources_include_lesson_link_when_available(self):
        store = FakeVectorStore(
            make_results([("text", "Course A", 2)]),
            lesson_links={("Course A", 2): "https://example.com/lesson2"},
            course_links={"Course A": "https://example.com/course"},
        )
        tool = CourseSearchTool(store)

        tool.execute(query="anything")

        assert tool.last_sources == [
            {"text": "Course A - Lesson 2", "link": "https://example.com/lesson2"}
        ]

    def test_sources_fall_back_to_course_link_when_no_lesson_link(self):
        store = FakeVectorStore(
            make_results([("text", "Course A", 2)]),
            lesson_links={},
            course_links={"Course A": "https://example.com/course"},
        )
        tool = CourseSearchTool(store)

        tool.execute(query="anything")

        assert tool.last_sources == [
            {"text": "Course A - Lesson 2", "link": "https://example.com/course"}
        ]

    def test_sources_link_is_none_when_no_lesson_number(self):
        store = FakeVectorStore(
            make_results([("text", "Course A", None)]),
            course_links={},  # no course link registered either
        )
        tool = CourseSearchTool(store)

        tool.execute(query="anything")

        assert tool.last_sources == [{"text": "Course A", "link": None}]

    def test_last_sources_reset_between_calls(self):
        store = FakeVectorStore(make_results([("text", "Course A", 1)]))
        tool = CourseSearchTool(store)
        tool.execute(query="first")
        assert len(tool.last_sources) == 1

        store.search_result = SearchResults(documents=[], metadata=[], distances=[])
        tool.execute(query="second, no results")

        assert tool.last_sources == [], (
            "last_sources is never cleared on an empty-result call, so a "
            "stale source list from a previous successful search can be "
            "reported to the UI for a query that actually found nothing."
        )

    def test_tool_manager_get_last_sources_and_reset(self):
        from search_tools import ToolManager

        store = FakeVectorStore(make_results([("text", "Course A", 1)]))
        tool = CourseSearchTool(store)
        manager = ToolManager()
        manager.register_tool(tool)

        manager.execute_tool("search_course_content", query="anything")
        assert manager.get_last_sources() != []

        manager.reset_sources()
        assert manager.get_last_sources() == []
