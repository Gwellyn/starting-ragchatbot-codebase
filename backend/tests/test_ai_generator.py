"""
Tests for AIGenerator (backend/ai_generator.py): does it correctly decide to
call CourseSearchTool via the Anthropic tool-use protocol, execute it through
the ToolManager, support up to 2 sequential tool-calling rounds, and feed
results back for a final answer?

The Anthropic SDK client is replaced with a MagicMock so no network calls are
made; only anthropic's response *shape* (content blocks, stop_reason) is
depended on, matching what `anthropic.Anthropic().messages.create()` returns.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import ai_generator
from ai_generator import AIGenerator


def text_block(text):
    return SimpleNamespace(type="text", text=text)


def tool_use_block(name, input, id="tool_1"):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def thinking_block(text="reasoning..."):
    return SimpleNamespace(type="thinking", text=text)


def response(stop_reason, content):
    return SimpleNamespace(stop_reason=stop_reason, content=content)


@pytest.fixture
def generator(monkeypatch):
    mock_client = MagicMock()
    monkeypatch.setattr(ai_generator.anthropic, "Anthropic", lambda api_key: mock_client)
    gen = AIGenerator(api_key="fake-key", model="claude-sonnet-5")
    return gen, mock_client


class TestNoToolNeeded:
    def test_general_question_returns_direct_text_without_tool_manager(self, generator):
        gen, client = generator
        client.messages.create.return_value = response("end_turn", [text_block("Paris is the capital of France.")])

        result = gen.generate_response(query="What is the capital of France?")

        assert result == "Paris is the capital of France."
        assert client.messages.create.call_count == 1

    def test_tools_are_attached_when_provided(self, generator):
        gen, client = generator
        client.messages.create.return_value = response("end_turn", [text_block("answer")])
        tools = [{"name": "search_course_content"}]

        gen.generate_response(query="anything", tools=tools)

        _, kwargs = client.messages.create.call_args
        assert kwargs["tools"] == tools
        assert kwargs["tool_choice"] == {"type": "auto"}

    def test_no_tools_key_when_tools_not_provided(self, generator):
        gen, client = generator
        client.messages.create.return_value = response("end_turn", [text_block("answer")])

        gen.generate_response(query="anything")

        _, kwargs = client.messages.create.call_args
        assert "tools" not in kwargs
        assert "tool_choice" not in kwargs


class TestToolInvocation:
    def test_calls_search_tool_with_claudes_arguments_via_tool_manager(self, generator):
        gen, client = generator
        tool_input = {"query": "What is MCP?", "course_name": "MCP"}
        client.messages.create.side_effect = [
            response("tool_use", [tool_use_block("search_course_content", tool_input)]),
            response("end_turn", [text_block("MCP is a protocol.")]),
        ]
        tool_manager = MagicMock()
        tool_manager.execute_tool.return_value = "[MCP Course - Lesson 1]\nMCP is..."

        result = gen.generate_response(
            query="What is MCP?",
            tools=[{"name": "search_course_content"}],
            tool_manager=tool_manager,
        )

        tool_manager.execute_tool.assert_called_once_with("search_course_content", **tool_input)
        assert result == "MCP is a protocol."
        assert client.messages.create.call_count == 2

    def test_round_two_call_still_has_tools_attached(self, generator):
        """
        After round 1's tool use, round 2 must still offer tools (Claude may
        need a second search) - tools are only stripped on the forced,
        no-tools final call after the round cap or a tool error.
        """
        gen, client = generator
        client.messages.create.side_effect = [
            response("tool_use", [tool_use_block("search_course_content", {"query": "x"})]),
            response("end_turn", [text_block("final answer")]),
        ]
        tool_manager = MagicMock()
        tool_manager.execute_tool.return_value = "search results"
        tools = [{"name": "search_course_content"}]

        gen.generate_response(query="x", tools=tools, tool_manager=tool_manager)

        second_call_kwargs = client.messages.create.call_args_list[1].kwargs
        assert second_call_kwargs["tools"] == tools
        assert second_call_kwargs["tool_choice"] == {"type": "auto"}

    def test_tool_result_message_references_correct_tool_use_id(self, generator):
        gen, client = generator
        client.messages.create.side_effect = [
            response("tool_use", [tool_use_block("search_course_content", {"query": "x"}, id="abc123")]),
            response("end_turn", [text_block("final answer")]),
        ]
        tool_manager = MagicMock()
        tool_manager.execute_tool.return_value = "search results"

        gen.generate_response(
            query="x", tools=[{"name": "search_course_content"}], tool_manager=tool_manager
        )

        second_call_messages = client.messages.create.call_args_list[1].kwargs["messages"]
        tool_result_message = second_call_messages[-1]
        assert tool_result_message["role"] == "user"
        assert tool_result_message["content"][0]["tool_use_id"] == "abc123"
        assert tool_result_message["content"][0]["content"] == "search results"

    def test_multiple_tool_calls_in_one_turn_are_all_executed(self, generator):
        gen, client = generator
        client.messages.create.side_effect = [
            response("tool_use", [
                tool_use_block("search_course_content", {"query": "a"}, id="id1"),
                tool_use_block("search_course_content", {"query": "b"}, id="id2"),
            ]),
            response("end_turn", [text_block("final answer")]),
        ]
        tool_manager = MagicMock()
        tool_manager.execute_tool.return_value = "results"

        gen.generate_response(
            query="x", tools=[{"name": "search_course_content"}], tool_manager=tool_manager
        )

        assert tool_manager.execute_tool.call_count == 2

    def test_no_tool_execution_when_tool_manager_missing_even_if_stop_reason_is_tool_use(self, generator):
        """
        Guards against a crash: if stop_reason=='tool_use' but no tool_manager
        was passed, generate_response must not try to handle tool execution,
        and must not retry (retrying an un-executable tool call can't help).
        """
        gen, client = generator
        client.messages.create.return_value = response(
            "tool_use", [tool_use_block("search_course_content", {"query": "x"})]
        )

        result = gen.generate_response(query="x", tools=[{"name": "search_course_content"}])

        assert result == ""
        assert client.messages.create.call_count == 1


class TestSequentialToolRounds:
    def test_two_sequential_tool_rounds_both_executed_final_call_excludes_tools(self, generator):
        gen, client = generator
        outline_input = {"course_name": "MCP"}
        search_input = {"query": "similar topic", "course_name": "Advanced MCP"}
        client.messages.create.side_effect = [
            response("tool_use", [tool_use_block("get_course_outline", outline_input, id="id1")]),
            response("tool_use", [tool_use_block("search_course_content", search_input, id="id2")]),
            response("end_turn", [text_block("Course X and Advanced MCP both cover transports.")]),
        ]
        tool_manager = MagicMock()
        tool_manager.execute_tool.side_effect = ["Lesson 4: Transports", "[Advanced MCP - Lesson 2]\n..."]
        tools = [{"name": "get_course_outline"}, {"name": "search_course_content"}]

        result = gen.generate_response(
            query="Find a course discussing the same topic as lesson 4 of MCP",
            tools=tools,
            tool_manager=tool_manager,
        )

        assert client.messages.create.call_count == 3
        assert tool_manager.execute_tool.call_count == 2
        tool_manager.execute_tool.assert_any_call("get_course_outline", **outline_input)
        tool_manager.execute_tool.assert_any_call("search_course_content", **search_input)
        assert result == "Course X and Advanced MCP both cover transports."

        third_call_kwargs = client.messages.create.call_args_list[2].kwargs
        assert "tools" not in third_call_kwargs
        assert "tool_choice" not in third_call_kwargs

    def test_hits_two_round_cap_forces_final_answer_without_further_tool_use(self, generator):
        gen, client = generator
        client.messages.create.side_effect = [
            response("tool_use", [tool_use_block("search_course_content", {"query": "a"}, id="id1")]),
            response("tool_use", [tool_use_block("search_course_content", {"query": "b"}, id="id2")]),
            response("end_turn", [text_block("Best answer given what I found.")]),
        ]
        tool_manager = MagicMock()
        tool_manager.execute_tool.return_value = "results"

        result = gen.generate_response(
            query="x", tools=[{"name": "search_course_content"}], tool_manager=tool_manager
        )

        assert client.messages.create.call_count == 3
        assert tool_manager.execute_tool.call_count == 2
        assert result == "Best answer given what I found."

    def test_messages_grow_correctly_across_two_rounds(self, generator):
        """
        The same `messages` list is mutated in place and threaded through
        every call (not rebuilt per round), so every recorded call_args
        entry aliases the same, final list - inspect that final state.
        """
        gen, client = generator
        client.messages.create.side_effect = [
            response("tool_use", [tool_use_block("search_course_content", {"query": "a"}, id="id1")]),
            response("tool_use", [tool_use_block("search_course_content", {"query": "b"}, id="id2")]),
            response("end_turn", [text_block("final answer")]),
        ]
        tool_manager = MagicMock()
        tool_manager.execute_tool.return_value = "results"

        gen.generate_response(
            query="x", tools=[{"name": "search_course_content"}], tool_manager=tool_manager
        )

        final_messages = client.messages.create.call_args_list[2].kwargs["messages"]
        assert [m["role"] for m in final_messages] == ["user", "assistant", "user", "assistant", "user"]
        assert final_messages[2]["content"][0]["tool_use_id"] == "id1"
        assert final_messages[4]["content"][0]["tool_use_id"] == "id2"

    def test_tool_execution_failure_terminates_rounds_and_produces_error_tool_result(self, generator):
        gen, client = generator
        client.messages.create.side_effect = [
            response("tool_use", [tool_use_block("search_course_content", {"query": "x"}, id="id1")]),
            response("end_turn", [text_block("I couldn't complete the search, but here's what I know.")]),
        ]
        tool_manager = MagicMock()
        tool_manager.execute_tool.side_effect = Exception("vector store unavailable")

        result = gen.generate_response(
            query="x", tools=[{"name": "search_course_content"}], tool_manager=tool_manager
        )

        # Round 2 never attempted: only round 1's call + the forced final call.
        assert client.messages.create.call_count == 2
        final_call_kwargs = client.messages.create.call_args_list[1].kwargs
        assert "tools" not in final_call_kwargs

        error_tool_result = final_call_kwargs["messages"][-1]["content"][0]
        assert error_tool_result["tool_use_id"] == "id1"
        assert error_tool_result["is_error"] is True
        assert "vector store unavailable" in error_tool_result["content"]

        assert result == "I couldn't complete the search, but here's what I know."

    def test_retry_after_tool_round_reissues_only_final_call_without_reexecuting_tools(self, generator):
        gen, client = generator
        client.messages.create.side_effect = [
            response("tool_use", [tool_use_block("search_course_content", {"query": "x"}, id="id1")]),
            response("end_turn", [text_block("Let me check that for you.")]),
            response("end_turn", [text_block("Here is the real answer.")]),
        ]
        tool_manager = MagicMock()
        tool_manager.execute_tool.return_value = "search results"

        result = gen.generate_response(
            query="x", tools=[{"name": "search_course_content"}], tool_manager=tool_manager
        )

        assert result == "Here is the real answer."
        assert client.messages.create.call_count == 3
        # The tool from round 1 must not be re-executed by the retry.
        assert tool_manager.execute_tool.call_count == 1


class TestIncompleteResponseRetry:
    def test_retries_when_first_attempt_is_a_stub_intent_sentence(self, generator):
        gen, client = generator
        client.messages.create.side_effect = [
            response("end_turn", [text_block("Let me check that for you.")]),
            response("end_turn", [text_block("Here is the real answer.")]),
        ]

        result = gen.generate_response(query="anything")

        assert result == "Here is the real answer."
        assert client.messages.create.call_count == 2

    def test_retries_when_response_text_is_empty(self, generator):
        gen, client = generator
        client.messages.create.side_effect = [
            response("end_turn", []),
            response("end_turn", [text_block("Here is the real answer.")]),
        ]

        result = gen.generate_response(query="anything")

        assert result == "Here is the real answer."
        assert client.messages.create.call_count == 2

    def test_gives_up_after_max_attempts_and_returns_last_result(self, generator):
        gen, client = generator
        client.messages.create.return_value = response("end_turn", [text_block("Let me look into it.")])

        result = gen.generate_response(query="anything")

        assert result == "Let me look into it."
        assert client.messages.create.call_count == 3

    def test_does_not_retry_a_long_answer_that_happens_to_start_with_ill(self, generator):
        long_answer = "I'll " + ("explain the whole concept in detail. " * 10)
        gen, client = generator
        client.messages.create.return_value = response("end_turn", [text_block(long_answer)])

        result = gen.generate_response(query="anything")

        assert result == long_answer
        assert client.messages.create.call_count == 1


class TestExtractText:
    def test_skips_thinking_blocks_and_joins_text_blocks(self):
        resp = response("end_turn", [thinking_block("secret reasoning"), text_block("visible answer")])
        assert AIGenerator._extract_text(resp) == "visible answer"

    def test_joins_multiple_text_blocks_with_blank_line(self):
        resp = response("end_turn", [text_block("part one"), text_block("part two")])
        assert AIGenerator._extract_text(resp) == "part one\n\npart two"


class TestConversationHistory:
    def test_history_is_embedded_in_system_prompt(self, generator):
        gen, client = generator
        client.messages.create.return_value = response("end_turn", [text_block("ok")])

        gen.generate_response(query="follow up", conversation_history="User: hi\nAssistant: hello")

        _, kwargs = client.messages.create.call_args
        assert "User: hi" in kwargs["system"]
        assert "Assistant: hello" in kwargs["system"]

    def test_no_history_section_when_history_is_none(self, generator):
        gen, client = generator
        client.messages.create.return_value = response("end_turn", [text_block("ok")])

        gen.generate_response(query="first question")

        _, kwargs = client.messages.create.call_args
        assert "Previous conversation" not in kwargs["system"]
