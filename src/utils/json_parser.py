"""JSON parsing utilities for LLM output."""

import json
import re
from typing import Any, cast


def parse_llm_json(text: str) -> dict[str, Any]:
    """Extract JSON from LLM output, handling markdown fences and extra text.

    Args:
        text: Raw LLM output that may contain JSON in markdown code blocks

    Returns:
        Parsed JSON as a dictionary

    Raises:
        json.JSONDecodeError: If JSON parsing fails
    """
    # Strip markdown code fences
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)

    # Find first { ... } block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return cast(dict[str, Any], json.loads(match.group()))

    return cast(dict[str, Any], json.loads(text))
