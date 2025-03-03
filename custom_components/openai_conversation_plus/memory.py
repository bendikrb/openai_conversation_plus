"""Memory utils."""

from __future__ import annotations

from typing import NotRequired, TypedDict


class MemorySettings(TypedDict):
    throttle_seconds: int
    message_history_length: int
    memory_min_score: float


class MemoryResult(TypedDict):
    id: str
    memory: str
    hash: str
    meta: NotRequired[dict[str, any]]
    score: float
    created_at: str
    updated_at: NotRequired[str]
    user_id: str


class MemoryRelation(TypedDict):
    source: str
    relationship: str
    destination: str


class MemorySearchResults(TypedDict):
    results: list[MemoryResult]
    relations: list[MemoryRelation]


def format_memories(memory_results: dict, score_threshold: float = 0.2) -> str:
    """Format memory results for the system prompt, filtering by score."""
    formatted_memories = []
    for result in memory_results.get("results", []):
        if result["score"] >= score_threshold:
            formatted_memories.append(  # noqa: PERF401
                f"- {result['memory']} (score: {result['score']:.2f})"
            )
    if formatted_memories:
        return "Relevant memories:\n" + "\n".join(formatted_memories)
    return ""
