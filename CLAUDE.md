# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

This project uses `uv` for Python dependency management (Python >= 3.13). Always use `uv` (e.g. `uv run`, `uv sync`, `uv add`) — do not use `pip` directly.

```bash
# Install dependencies
uv sync

# Run the application (from project root)
chmod +x run.sh && ./run.sh

# Run manually (from backend/)
cd backend && uv run uvicorn app:app --reload --port 8000
```

Requires a `.env` file in the project root with `ANTHROPIC_API_KEY=...` (see `.env.example`).

Once running: web UI at `http://localhost:8000`, API docs at `http://localhost:8000/docs`.

There is no test suite, linter, or build step configured in this repo.

## Architecture

This is a Retrieval-Augmented Generation (RAG) chatbot that answers questions about course materials. FastAPI backend (`backend/`), vanilla JS frontend (`frontend/`, served directly as static files by FastAPI), ChromaDB for vector storage, `sentence-transformers` (`all-MiniLM-L6-v2`) for embeddings, and Anthropic's Claude for generation.

### Request flow

`frontend/script.js` → `POST /api/query` → `app.py` → `RAGSystem.query()` (`rag_system.py`, the central orchestrator) → `AIGenerator.generate_response()` (`ai_generator.py`), which calls Claude with the `search_course_content` tool available.

Claude decides whether to search:
- **General knowledge questions** are answered directly, no tool call.
- **Course-specific questions** trigger a tool call. `AIGenerator._handle_tool_execution()` runs the tool via `ToolManager` → `CourseSearchTool.execute()` (`search_tools.py`) → `VectorStore.search()` (`vector_store.py`), then makes a **second** Claude call (tools disabled) with the tool results appended, to synthesize the final answer. At most one search round-trip is expected per query — this is enforced only via system-prompt instruction, not code.

Conversation history is in-memory per session (`SessionManager` in `session_manager.py`, capped at `MAX_HISTORY` exchanges) and is passed to Claude as text embedded in the system prompt, not as multi-turn message history.

### Vector store (`vector_store.py`)

Two separate ChromaDB collections:
- `course_catalog` — one entry per course (title as ID, instructor, course link, lessons serialized as a `lessons_json` string). Used for fuzzy/semantic course-name resolution (e.g. matching "MCP" to a full course title) before filtering the content search.
- `course_content` — the actual chunked course text, embedded and searchable, filterable by resolved `course_title` and/or `lesson_number`.

`CourseSearchTool.execute()` resolves `course_name` against `course_catalog` first (if provided), then queries `course_content` with the resolved title as a filter. Search results are formatted with `[Course - Lesson N]` headers, and the underlying source list is tracked on `CourseSearchTool.last_sources` for the frontend to display, then reset after each query via `ToolManager.reset_sources()`.

### Document ingestion (`document_processor.py`)

On startup, `app.py` loads every `.pdf`/`.docx`/`.txt` file in `docs/` via `RAGSystem.add_course_folder()`, skipping any course whose title already exists in the vector store.

Expected document format:
```
Course Title: <title>
Course Link: <url>
Course Instructor: <name>

Lesson 0: <lesson title>
Lesson Link: <url>
<lesson content...>

Lesson 1: <lesson title>
...
```

Lessons are parsed via `Lesson N: <title>` markers. Each lesson's text is split into sentence-aware chunks (`chunk_text()`, regex-based sentence splitting that avoids breaking on abbreviations) sized by `config.CHUNK_SIZE`/`CHUNK_OVERLAP`. The first chunk of each non-final lesson and every chunk of the final lesson processed get a `"Lesson N content: ..."` / `"Course <title> Lesson N content: ..."` prefix so chunks are self-contained when retrieved out of context — note this prefixing logic is inconsistent between the two code paths (mid-loop vs. final lesson).

### Configuration

All tunables live in `backend/config.py` as a single dataclass (`config`): `ANTHROPIC_MODEL`, `EMBEDDING_MODEL`, `CHUNK_SIZE`/`CHUNK_OVERLAP`, `MAX_RESULTS` (search results returned), `MAX_HISTORY` (conversation exchanges retained), `CHROMA_PATH`.
