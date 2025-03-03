"""Memory utils."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mem0 import AsyncMemoryClient

os.environ["MEM0_TELEMETRY"] = "false"

def get_memory_client(host: str | None = None, api_key: str | None = None) -> AsyncMemoryClient:
    from mem0 import AsyncMemoryClient

    conf = {
        "api_key": api_key,
        "host": host,
    }
    return AsyncMemoryClient(**conf)


def format_memories(memory_results: dict, score_threshold: float = 0.2) -> str:
    """Format memory results for the system prompt, filtering by score."""
    formatted_memories = []
    for result in memory_results.get("results", []):
        if result["score"] >= score_threshold:
            formatted_memories.append(f"- {result['memory']} (score: {result['score']:.2f})")  # noqa: PERF401
    if formatted_memories:
        return "Relevant memories:\n" + "\n".join(formatted_memories)
    return ""
