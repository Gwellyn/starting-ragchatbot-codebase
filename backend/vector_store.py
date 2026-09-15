import chromadb
from chromadb.config import Settings
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from models import Course, CourseChunk
from sentence_transformers import SentenceTransformer


@dataclass
class SearchResults:
    """Container for search results with metadata"""

    documents: List[str]
    metadata: List[Dict[str, Any]]
    distances: List[float]
    error: Optional[str] = None

    @classmethod
    def from_chroma(cls, chroma_results: Dict) -> "SearchResults":
        """Create SearchResults from ChromaDB query results"""
        return cls(
            documents=(
                chroma_results["documents"][0] if chroma_results["documents"] else []
            ),
            metadata=(
                chroma_results["metadatas"][0] if chroma_results["metadatas"] else []
            ),
            distances=(
                chroma_results["distances"][0] if chroma_results["distances"] else []
            ),
        )

    @classmethod
    def empty(cls, error_msg: str) -> "SearchResults":
        """Create empty results with error message"""
        return cls(documents=[], metadata=[], distances=[], error=error_msg)

    def is_empty(self) -> bool:
        """Check if results are empty"""
        return len(self.documents) == 0


class VectorStore:
    """Vector storage using ChromaDB for course content and metadata"""

    def __init__(self, chroma_path: str, embedding_model: str, max_results: int = 5):
        self.max_results = max_results
        # Initialize ChromaDB client
        self.client = chromadb.PersistentClient(
            path=chroma_path, settings=Settings(anonymized_telemetry=False)
        )

        # Set up sentence transformer embedding function
        self.embedding_function = (
            chromadb.utils.embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=embedding_model
            )
        )

        # Create collections for different types of data
        self.course_catalog = self._create_collection(
            "course_catalog"
        )  # Course titles/instructors
        self.course_content = self._create_collection(
            "course_content"
        )  # Actual course material

    def _create_collection(self, name: str):
        """Create or get a ChromaDB collection"""
        return self.client.get_or_create_collection(
            name=name, embedding_function=self.embedding_function
        )

    def search(
        self,
        query: str,
        course_name: Optional[str] = None,
        lesson_number: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> SearchResults:
        """
        Main search interface that handles course resolution and content search.

        Args:
            query: What to search for in course content
            course_name: Optional course name/title to filter by
            lesson_number: Optional lesson number to filter by
            limit: Maximum results to return

        Returns:
            SearchResults object with documents and metadata
        """
        # Step 1: Resolve course name if provided
        course_title = None
        if course_name:
            course_title = self._resolve_course_name(course_name)
            if not course_title:
                return SearchResults.empty(f"No course found matching '{course_name}'")

        # Step 2: Build filter for content search
        filter_dict = self._build_filter(course_title, lesson_number)

        # Step 3: Search course content
        # Use provided limit or fall back to configured max_results
        search_limit = limit if limit is not None else self.max_results

        # When a specific lesson isn't pinned, one lesson's near-duplicate
        # chunks can dominate the top results (e.g. a "conclusion" lesson
        # that recaps the whole course crowding out every other lesson for
        # an "outline" style query). Pull a larger candidate pool and cap
        # how many chunks per lesson make the final cut, so results span
        # more of the course. Skip this when lesson_number is pinned - every
        # candidate would already share the same lesson, so capping would
        # just throw away otherwise-relevant results.
        diversify = lesson_number is None
        candidate_limit = max(search_limit * 4, 20) if diversify else search_limit

        try:
            results = self.course_content.query(
                query_texts=[query], n_results=candidate_limit, where=filter_dict
            )
            parsed = SearchResults.from_chroma(results)
            return self._diversify(parsed, search_limit) if diversify else parsed
        except Exception as e:
            return SearchResults.empty(f"Search error: {str(e)}")

    @staticmethod
    def _diversify(
        results: "SearchResults", limit: int, per_lesson_cap: int = 2
    ) -> "SearchResults":
        """
        Trim relevance-ranked results down to `limit`, capping how many chunks
        can come from the same (course, lesson) so one lesson's near-duplicate
        chunks don't crowd out the rest of the course. Backfills with the next
        best results, still in relevance order, if capping leaves us short.
        """
        if results.is_empty() or len(results.documents) <= limit:
            return results

        counts: Dict[Any, int] = {}
        selected_idx = []
        deferred_idx = []
        for i, meta in enumerate(results.metadata):
            key = (meta.get("course_title"), meta.get("lesson_number"))
            if counts.get(key, 0) < per_lesson_cap:
                selected_idx.append(i)
                counts[key] = counts.get(key, 0) + 1
            else:
                deferred_idx.append(i)

        selected_idx = selected_idx[:limit]
        if len(selected_idx) < limit:
            selected_idx.extend(deferred_idx[: limit - len(selected_idx)])
        selected_idx.sort()  # restore original relevance order

        return SearchResults(
            documents=[results.documents[i] for i in selected_idx],
            metadata=[results.metadata[i] for i in selected_idx],
            distances=[results.distances[i] for i in selected_idx],
        )

    def _resolve_course_name(self, course_name: str) -> Optional[str]:
        """Use vector search to find best matching course by name"""
        try:
            results = self.course_catalog.query(query_texts=[course_name], n_results=1)

            if results["documents"][0] and results["metadatas"][0]:
                # Return the title (which is now the ID)
                return results["metadatas"][0][0]["title"]
        except Exception as e:
            print(f"Error resolving course name: {e}")

        return None

    def _build_filter(
        self, course_title: Optional[str], lesson_number: Optional[int]
    ) -> Optional[Dict]:
        """Build ChromaDB filter from search parameters"""
        if not course_title and lesson_number is None:
            return None

        # Handle different filter combinations
        if course_title and lesson_number is not None:
            return {
                "$and": [
                    {"course_title": course_title},
                    {"lesson_number": lesson_number},
                ]
            }

        if course_title:
            return {"course_title": course_title}

        return {"lesson_number": lesson_number}

    def add_course_metadata(self, course: Course):
        """Add course information to the catalog for semantic search"""
        import json

        course_text = course.title

        # Build lessons metadata and serialize as JSON string
        lessons_metadata = []
        for lesson in course.lessons:
            lessons_metadata.append(
                {
                    "lesson_number": lesson.lesson_number,
                    "lesson_title": lesson.title,
                    "lesson_link": lesson.lesson_link,
                }
            )

        self.course_catalog.add(
            documents=[course_text],
            metadatas=[
                {
                    "title": course.title,
                    "instructor": course.instructor,
                    "course_link": course.course_link,
                    "lessons_json": json.dumps(
                        lessons_metadata
                    ),  # Serialize as JSON string
                    "lesson_count": len(course.lessons),
                }
            ],
            ids=[course.title],
        )

    def add_course_content(self, chunks: List[CourseChunk]):
        """Add course content chunks to the vector store"""
        if not chunks:
            return

        documents = [chunk.content for chunk in chunks]
        metadatas = [
            {
                "course_title": chunk.course_title,
                "lesson_number": chunk.lesson_number,
                "chunk_index": chunk.chunk_index,
            }
            for chunk in chunks
        ]
        # Use title with chunk index for unique IDs
        ids = [
            f"{chunk.course_title.replace(' ', '_')}_{chunk.chunk_index}"
            for chunk in chunks
        ]

        self.course_content.add(documents=documents, metadatas=metadatas, ids=ids)

    def clear_all_data(self):
        """Clear all data from both collections"""
        try:
            self.client.delete_collection("course_catalog")
            self.client.delete_collection("course_content")
            # Recreate collections
            self.course_catalog = self._create_collection("course_catalog")
            self.course_content = self._create_collection("course_content")
        except Exception as e:
            print(f"Error clearing data: {e}")

    def get_existing_course_titles(self) -> List[str]:
        """Get all existing course titles from the vector store"""
        try:
            # Get all documents from the catalog
            results = self.course_catalog.get()
            if results and "ids" in results:
                return results["ids"]
            return []
        except Exception as e:
            print(f"Error getting existing course titles: {e}")
            return []

    def get_course_count(self) -> int:
        """Get the total number of courses in the vector store"""
        try:
            results = self.course_catalog.get()
            if results and "ids" in results:
                return len(results["ids"])
            return 0
        except Exception as e:
            print(f"Error getting course count: {e}")
            return 0

    def get_all_courses_metadata(self) -> List[Dict[str, Any]]:
        """Get metadata for all courses in the vector store"""
        import json

        try:
            results = self.course_catalog.get()
            if results and "metadatas" in results:
                # Parse lessons JSON for each course
                parsed_metadata = []
                for metadata in results["metadatas"]:
                    course_meta = metadata.copy()
                    if "lessons_json" in course_meta:
                        course_meta["lessons"] = json.loads(course_meta["lessons_json"])
                        del course_meta[
                            "lessons_json"
                        ]  # Remove the JSON string version
                    parsed_metadata.append(course_meta)
                return parsed_metadata
            return []
        except Exception as e:
            print(f"Error getting courses metadata: {e}")
            return []

    def get_course_link(self, course_title: str) -> Optional[str]:
        """Get course link for a given course title"""
        try:
            # Get course by ID (title is the ID)
            results = self.course_catalog.get(ids=[course_title])
            if results and "metadatas" in results and results["metadatas"]:
                metadata = results["metadatas"][0]
                return metadata.get("course_link")
            return None
        except Exception as e:
            print(f"Error getting course link: {e}")
            return None

    def get_course_outline(self, course_name: str) -> Optional[Dict[str, Any]]:
        """
        Resolve a (possibly partial) course name and return its outline:
        title, course link, and the full lesson list (number + title).
        """
        import json

        course_title = self._resolve_course_name(course_name)
        if not course_title:
            return None

        try:
            results = self.course_catalog.get(ids=[course_title])
            if not results or not results.get("metadatas"):
                return None

            metadata = results["metadatas"][0]
            lessons_json = metadata.get("lessons_json")
            lessons = json.loads(lessons_json) if lessons_json else []

            return {
                "title": metadata.get("title", course_title),
                "course_link": metadata.get("course_link"),
                "lessons": [
                    {
                        "lesson_number": lesson.get("lesson_number"),
                        "lesson_title": lesson.get("lesson_title"),
                    }
                    for lesson in lessons
                ],
            }
        except Exception as e:
            print(f"Error getting course outline: {e}")
            return None

    def get_lesson_link(self, course_title: str, lesson_number: int) -> Optional[str]:
        """Get lesson link for a given course title and lesson number"""
        import json

        try:
            # Get course by ID (title is the ID)
            results = self.course_catalog.get(ids=[course_title])
            if results and "metadatas" in results and results["metadatas"]:
                metadata = results["metadatas"][0]
                lessons_json = metadata.get("lessons_json")
                if lessons_json:
                    lessons = json.loads(lessons_json)
                    # Find the lesson with matching number
                    for lesson in lessons:
                        if lesson.get("lesson_number") == lesson_number:
                            return lesson.get("lesson_link")
            return None
        except Exception as e:
            print(f"Error getting lesson link: {e}")
