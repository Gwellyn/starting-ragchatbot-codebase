import re
import anthropic
from typing import List, Optional, Dict, Any, Tuple

# Occasionally the model ends its final (no-tools) turn with an empty response,
# or with a stub announcing a follow-up action it can't actually take (no tools
# are available on that call) instead of an answer. Both are treated as
# incomplete and retried.
_STUB_INTENT_RE = re.compile(
    r"^(let me|i'll|i will|i need to|i'm going to|i plan to|allow me to|one moment)\b",
    re.IGNORECASE,
)

class AIGenerator:
    """Handles interactions with Anthropic's Claude API for generating responses"""

    # Static system prompt to avoid rebuilding on each call
    SYSTEM_PROMPT = """ You are an AI assistant specialized in course materials and educational content with access to tools for course information.

Tool Usage:
- **search_course_content**: Use for questions about specific course content or detailed educational materials (e.g. what a lesson explains or teaches)
- **get_course_outline**: Use for questions about a course's structure — its title, course link, or lesson list (e.g. "what lessons are in X", "give me the outline of Y")
- **Up to 2 sequential tool-call rounds per query.** After seeing a tool's results, you may call another tool if you need more information it revealed (e.g., look up a course outline to find a lesson title, then search that lesson's content) — but don't call a tool redundantly or speculatively.
- Synthesize tool results into accurate, fact-based responses
- If a tool yields no results, state this clearly without offering alternatives
- Once you have enough information, answer directly without further tool calls — no further tools are available after round 2.

Response Protocol:
- **General knowledge questions**: Answer using existing knowledge without using tools
- **Course-specific questions**: Use the appropriate tool(s) first, then answer
- **Outline/structure questions**: When using get_course_outline, always include the course title, course link, and every lesson's number and title in the response
- **No meta-commentary**:
 - Provide direct answers only — no reasoning process, search explanations, or question-type analysis
 - Do not mention "based on the search results"


All responses must be:
1. **Brief, Concise and focused** - Get to the point quickly
2. **Educational** - Maintain instructional value
3. **Clear** - Use accessible language
4. **Example-supported** - Include relevant examples when they aid understanding
Provide only the direct answer to what was asked.
"""

    # Hard cap on sequential tool-calling rounds per user query (see
    # generate_response). Enforced in code, not just via the system prompt.
    MAX_TOOL_ROUNDS = 2

    def __init__(self, api_key: str, model: str):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

        # Pre-build base API parameters
        self.base_params = {
            "model": self.model,
            "max_tokens": 800,
            "thinking": {"type": "disabled"}
        }

    def generate_response(self, query: str,
                         conversation_history: Optional[str] = None,
                         tools: Optional[List] = None,
                         tool_manager=None) -> str:
        """
        Generate AI response, allowing Claude to make up to MAX_TOOL_ROUNDS
        sequential tool calls (each a separate API round, reasoning over the
        previous round's tool results) before producing a final answer.

        Args:
            query: The user's question or request
            conversation_history: Previous messages for context
            tools: Available tools the AI can use
            tool_manager: Manager to execute tools

        Returns:
            Generated response as string
        """

        # Build system content efficiently - avoid string ops when possible
        system_content = (
            f"{self.SYSTEM_PROMPT}\n\nPrevious conversation:\n{conversation_history}"
            if conversation_history
            else self.SYSTEM_PROMPT
        )

        messages: List[Dict[str, Any]] = [{"role": "user", "content": query}]

        for _ in range(self.MAX_TOOL_ROUNDS):
            api_params = {
                **self.base_params,
                "messages": messages,
                "system": system_content,
            }
            if tools:
                api_params["tools"] = tools
                api_params["tool_choice"] = {"type": "auto"}

            response = self.client.messages.create(**api_params)

            if response.stop_reason != "tool_use":
                # Claude is done - no further tool calls requested.
                return self._retry_if_incomplete(api_params, response)

            if not tool_manager:
                # Can't execute tools without a manager; nothing further to do.
                return self._extract_text(response)

            messages.append({"role": "assistant", "content": response.content})
            tool_results, errored = self._execute_tools(response, tool_manager)
            messages.append({"role": "user", "content": tool_results})

            if errored:
                # A tool call failed - stop requesting more tool use and let
                # Claude respond to the error with whatever it already has.
                break

        # Either MAX_TOOL_ROUNDS were used up, or a tool call errored: make a
        # final call with no tools attached so Claude must answer directly.
        final_params = {
            **self.base_params,
            "messages": messages,
            "system": system_content,
        }
        final_response = self.client.messages.create(**final_params)
        return self._retry_if_incomplete(final_params, final_response)

    def _execute_tools(self, response, tool_manager) -> Tuple[List[Dict[str, Any]], bool]:
        """
        Execute every tool_use block in a response via tool_manager.

        Returns (tool_results, errored): tool_results is the list of
        tool_result content blocks (in the order the tools were called,
        including any that failed), and errored is True if any tool call
        raised an exception. A failed call still produces a tool_result
        (marked is_error) so the message list stays valid for the API.
        """
        tool_results = []
        errored = False
        for content_block in response.content:
            if content_block.type != "tool_use":
                continue

            try:
                tool_result = tool_manager.execute_tool(
                    content_block.name,
                    **content_block.input
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": content_block.id,
                    "content": tool_result,
                })
            except Exception as e:
                errored = True
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": content_block.id,
                    "content": f"Tool '{content_block.name}' failed: {e}",
                    "is_error": True,
                })

        return tool_results, errored

    def _retry_if_incomplete(self, api_params: Dict[str, Any], response) -> str:
        """
        If the response's text is empty or a stub intent sentence, retry the
        same call (identical params, so no tools are re-invoked) up to
        max_attempts times. This retries only the final synthesis call, not
        any preceding tool-calling rounds.
        """
        max_attempts = 3
        result = self._extract_text(response)
        attempts = 1
        while self._is_incomplete(result) and attempts < max_attempts:
            response = self.client.messages.create(**api_params)
            result = self._extract_text(response)
            attempts += 1
        return result

    @staticmethod
    def _is_incomplete(text: str) -> bool:
        """True if text is empty, or a short stub announcing an action instead of answering."""
        text = text.strip()
        if not text:
            return True
        return len(text) < 200 and bool(_STUB_INTENT_RE.match(text))

    @staticmethod
    def _extract_text(response) -> str:
        """Concatenate all text blocks in a response, skipping non-text blocks (e.g. thinking blocks)."""
        return "\n\n".join(
            block.text for block in response.content if block.type == "text"
        )
